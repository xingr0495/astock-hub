# astock_hub/store/qfq_refresh.py
# -*- coding: utf-8 -*-
"""每日前复权增量（qfq 修复配套，2026-11-12 立项步骤③）。

因子不变假设（大多数交易日成立）：qfq(D) = qfq(D-1) × raw(D)/raw(D-1)。
raw 向下跳空 >1.5% 的票需要确认是否除权；确认改为**批量并行**拉取并
优先读 `adj_factor.parquet` 缓存（2026-11-13 性能修复：此前逐票串行，
大跌日 3524 只疑似票跑约 106 分钟）。因子真变的票汇总后**一次**重写
qfq 主库，避免逐票读写全库。因子拉不到的票按普通行情缩放 append（原语义）。

挂接：parquet_store.sync_store 写完 raw 后调用 refresh_qfq_daily(cfg)。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

SUSPECT_GAP = -0.015  # 向下跳空阈值：除权缺口（含 1-2% 小额分红）+ 普通大跌误拉
_BATCH_SIZE = 10       # stock_adj_factor_tdx 支持逗号多代码（build_kline_qfq 同口径）
_MAX_WORKERS = 6       # 有界并发，避免把 TDX 因子端点打成风控


def refresh_qfq_daily(cfg: dict) -> dict:
    """raw 更新后 qfq 增量。返回 {ok, days, appended, rebuilt, ...}。"""
    from . import parquet_store
    store = parquet_store._store_dir(cfg)
    raw_file = store / "kline_daily_raw" / "_all.parquet"
    qfq_file = store / "kline_daily" / "_all.parquet"
    if not raw_file.exists() or not qfq_file.exists():
        return {"ok": False,
                "reason": "raw 或 qfq 库缺失（先跑 scripts.build_kline_qfq）"}

    raw = _load(raw_file)
    qfq = _load(qfq_file)
    raw_last = raw["_d"].max()
    qfq_last = qfq["_d"].max()
    if not raw_last or not qfq_last or qfq_last >= raw_last:
        return {"ok": True, "message": "qfq 已是最新", "qfq_to": qfq_last}

    # 唯一日期用 polars 原生 unique 去重后取小列表——raw["_d"].to_list() 会把
    # 800 万行日期字符串整个转 Python list（约 800MB 对象），16:00 链内存紧张时
    # 触发 polars 空指针 PanicException（2026-11-19 断更根因）；unique 在 Rust 层
    # 去重，仅 ~1700 个日期回 Python，快且不再偶发崩溃。
    raw_days = set(raw["_d"].unique().to_list())
    new_days = sorted(d for d in raw_days - set(qfq["_d"].unique().to_list())
                      if d > qfq_last)
    if not new_days:
        return {"ok": True, "message": "无新增交易日"}

    all_days = sorted(raw_days)  # 循环内复用，不再逐日重复 to_list

    cache_df = _load_factor_cache(store)
    rebuild_data: dict[str, dict] = {}
    fetched_frames: list[pl.DataFrame] = []
    appended = 0
    total_batches = 0
    add_frames: list[pl.DataFrame] = []

    for day in new_days:
        prev = max(d for d in all_days if d < day)
        raw_day = raw.filter(pl.col("_d") == day)
        raw_prev = (raw.filter(pl.col("_d") == prev)
                    .select(["symbol", "close"])
                    .rename({"close": "_prev_close"}))
        m = raw_day.join(raw_prev, on="symbol", how="inner")
        if m.is_empty():
            continue
        m = m.with_columns((pl.col("close") / pl.col("_prev_close") - 1)
                           .alias("_gap"))
        suspects = m.filter(pl.col("_gap") < SUSPECT_GAP)["symbol"].to_list()

        # 缓存覆盖 prev/day 的票不外呼；其余按批量并行确认
        cached_pairs = _cached_factor_pairs(cache_df, suspects, prev, day)
        todo = [s for s in suspects if s not in cached_pairs]
        total_batches += (len(todo) + _BATCH_SIZE - 1) // _BATCH_SIZE
        changed, frames = _fetch_factors_parallel(todo, prev, day)
        fetched_frames.extend(frames)

        for sym in suspects:
            if sym in rebuild_data:
                continue
            if sym in changed:
                # 新拉取结果带完整因子历史，直接供后续汇总重算
                rebuild_data[sym] = changed[sym]
                continue
            fd = cached_pairs.get(sym) or {}
            h_prev, h_day = fd.get(prev), fd.get(day)
            if h_prev and h_day and abs(h_day - h_prev) > 1e-12:
                full = _history_from_cache(cache_df, sym)
                if full:
                    rebuild_data[sym] = full

        # 未重建的票：因子不变缩放 append（qfq_close = 前日 qfq × raw 比）
        done = set(qfq.filter(pl.col("_d") == day)["symbol"].to_list())
        scale = m.filter(~pl.col("symbol").is_in(
            list(rebuild_data) + list(done)))
        if scale.is_empty():
            continue
        qfq_prev = (qfq.filter(pl.col("_d") == prev)
                    .select(["symbol", "open", "high", "low", "close"])
                    .rename({c: f"_{c}" for c in
                             ("open", "high", "low", "close")}))
        add = scale.join(qfq_prev, on="symbol", how="inner")
        if add.is_empty():
            continue
        ratio = pl.col("close") / pl.col("_prev_close")
        add = add.with_columns([
            (pl.col("_open") * ratio).alias("open"),
            (pl.col("_high") * ratio).alias("high"),
            (pl.col("_low") * ratio).alias("low"),
            (pl.col("_close") * ratio).alias("close"),
            pl.lit(day).alias("_d"),
        ]).with_columns(pl.col("_d").str.to_date().alias("date"))
        add = add.select(["symbol", "date", "open", "high", "low", "close",
                          "volume", "amount", "_d"])
        add_frames.append(add)
        appended += add.height

    final = qfq
    if add_frames:
        final = pl.concat([final, *add_frames], how="vertical_relaxed")
    if rebuild_data:
        final = _apply_rebuilds(final, raw, rebuild_data)
    out = (final.drop("_d")
           .unique(subset=["symbol", "date"], keep="last")
           .sort(["symbol", "date"]))
    tmp = qfq_file.with_suffix(".parquet.tmp")
    out.write_parquet(tmp)
    os.replace(tmp, qfq_file)  # 全程只写一次 qfq 主库
    _save_factor_cache(store, cache_df, fetched_frames)
    logger.info("qfq 增量完成：新增 %d 行，除权重算 %d 只，因子批量 %d 批",
                appended, len(rebuild_data), total_batches)
    return {"ok": True, "days": len(new_days), "appended": appended,
            "rebuilt": sorted(rebuild_data), "qfq_to": raw_last}


def _client():
    """axdata 客户端工厂（测试可 patch；每个批量 worker 独立实例）。"""
    from axdata import AxDataClient
    return AxDataClient()


def _load(path: Path) -> pl.DataFrame:
    df = pl.read_parquet(path)
    if "thscode" in df.columns and "symbol" not in df.columns:
        df = df.rename({"thscode": "symbol"})
    return df.with_columns(
        pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("_d"))


def _load_factor_cache(store: Path) -> pl.DataFrame:
    """读取 data/market/adj_factor.parquet；统一 symbol/date/adj_factor 三列。"""
    p = store / "adj_factor.parquet"
    if not p.exists():
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Utf8,
                                    "adj_factor": pl.Float64})
    df = pl.read_parquet(p)
    if "ts_code" in df.columns and "symbol" not in df.columns:
        df = df.rename({"ts_code": "symbol"})
    return df.with_columns(
        pl.col("date").cast(pl.Utf8).str.slice(0, 10)).select(
        ["symbol", "date", "adj_factor"])


def _cached_factor_pairs(cache_df: pl.DataFrame, symbols: list[str],
                         prev: str, day: str) -> dict[str, dict]:
    """返回缓存里 prev/day 都覆盖的 {symbol: {date: factor}}。"""
    if cache_df.is_empty() or not symbols:
        return {}
    sub = cache_df.filter(
        pl.col("symbol").is_in(symbols)
        & pl.col("date").is_in([prev, day]))
    pairs: dict[str, dict] = {}
    for row in sub.iter_rows(named=True):
        pairs.setdefault(row["symbol"], {})[row["date"]] = row["adj_factor"]
    return {s: v for s, v in pairs.items() if prev in v and day in v}


def _history_from_cache(cache_df: pl.DataFrame, sym: str) -> dict:
    """缓存里某只票的完整因子历史（仅缓存判出真除权时调用）。"""
    if cache_df.is_empty():
        return {}
    sub = cache_df.filter(pl.col("symbol") == sym)
    return dict(zip(sub["date"].to_list(), sub["adj_factor"].to_list()))


def _normalize_factor_pdf(pdf, symbols: list[str]) -> pl.DataFrame:
    """axdata 返回 → symbol/date/adj_factor（单代码无 ts_code 时补列）。"""
    if pdf is None or len(pdf) == 0:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Utf8,
                                    "adj_factor": pl.Float64})
    if "ts_code" not in pdf.columns:
        if len(symbols) != 1:
            return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Utf8,
                                        "adj_factor": pl.Float64})
        pdf = pdf.assign(ts_code=symbols[0])
    sub = pl.from_pandas(pdf[["ts_code", "trade_date", "adj_factor"]])
    return sub.rename({"ts_code": "symbol"}).with_columns(
        (pl.col("trade_date").str.slice(0, 4) + "-"
         + pl.col("trade_date").str.slice(4, 2) + "-"
         + pl.col("trade_date").str.slice(6, 2)).alias("date")
    ).select(["symbol", "date", "adj_factor"])


def _fetch_batch_hfq(symbols: list[str]) -> pl.DataFrame:
    """一批代码拉因子（worker 独立 client），失败返回空表不阻塞整链。"""
    try:
        client = _client()
        pdf = client.call("stock_adj_factor_tdx",
                          code=",".join(symbols), adjust="hfq")
    except Exception as e:  # noqa: BLE001 — 批失败按普通行情处理
        logger.warning("因子批量拉取失败 %s: %s", symbols, e)
        return pl.DataFrame(schema={"symbol": pl.Utf8, "date": pl.Utf8,
                                    "adj_factor": pl.Float64})
    return _normalize_factor_pdf(pdf, symbols)


def _df_to_hfq_map(df: pl.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in df.iter_rows(named=True):
        out.setdefault(row["symbol"], {})[row["date"]] = row["adj_factor"]
    return out


def _fetch_factors_parallel(symbols: list[str], prev: str, day: str
                            ) -> tuple[dict[str, dict], list[pl.DataFrame]]:
    """批量并行确认：返回(真除权 {symbol: 完整hfq}, 新因子帧列表)。"""
    from concurrent.futures import ThreadPoolExecutor

    if not symbols:
        return {}, []
    batches = [symbols[i:i + _BATCH_SIZE]
               for i in range(0, len(symbols), _BATCH_SIZE)]
    changed: dict[str, dict] = {}
    frames: list[pl.DataFrame] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for df in pool.map(_fetch_batch_hfq, batches):
            if df.is_empty():
                continue
            frames.append(df)
            for sym, fd in _df_to_hfq_map(df).items():
                h_prev, h_day = fd.get(prev), fd.get(day)
                if h_prev and h_day and abs(h_day - h_prev) > 1e-12:
                    changed[sym] = fd
    return changed, frames


def _save_factor_cache(store: Path, cache_df: pl.DataFrame,
                       new_frames: list[pl.DataFrame]) -> None:
    """新拉因子合并回 adj_factor.parquet（同 ts_code+date 去重，原子写）。"""
    if not new_frames:
        return
    path = store / "adj_factor.parquet"
    new = pl.concat(new_frames, how="vertical_relaxed").rename(
        {"symbol": "ts_code"})
    if cache_df.is_empty():
        merged = new
    else:
        merged = pl.concat([
            cache_df.rename({"symbol": "ts_code"}), new],
            how="vertical_relaxed")
    merged = merged.unique(subset=["ts_code", "date"], keep="last")
    tmp = path.with_suffix(".parquet.tmp")
    merged.write_parquet(tmp)
    os.replace(tmp, path)


def _rebuild_rows(raw: pl.DataFrame, sym: str, hfq: dict) -> pl.DataFrame | None:
    """单票 qfq 全历史重算（纯计算，不读写文件）。"""
    r = raw.filter(pl.col("symbol") == sym).sort("_d")
    if r.is_empty():
        return None
    dates = r["_d"].to_list()
    anchor = next((d for d in dates if d >= "2020-01-02" and d in hfq), None)
    if anchor is None:
        return None
    h_a = hfq[anchor]
    rows = []
    for d, o, h, l, c, v, a in zip(dates, r["open"].to_list(),
                                   r["high"].to_list(), r["low"].to_list(),
                                   r["close"].to_list(),
                                   r["volume"].to_list(),
                                   r["amount"].to_list()):
        fh = hfq.get(d)
        if fh is None:
            continue
        ratio = fh / h_a
        rows.append({"symbol": sym, "date": d, "_d": d,
                     "open": o * ratio, "high": h * ratio,
                     "low": l * ratio, "close": c * ratio,
                     "volume": v, "amount": a})
    if not rows:
        return None
    return pl.DataFrame(rows).with_columns(pl.col("date").str.to_date())


def _apply_rebuilds(final: pl.DataFrame, raw: pl.DataFrame,
                    rebuild_data: dict[str, dict]) -> pl.DataFrame:
    """把所有除权票一次合并进 qfq 帧，避免逐票读写全库。"""
    keep = final.filter(~pl.col("symbol").is_in(list(rebuild_data)))
    frames = []
    for sym, hfq in rebuild_data.items():
        new = _rebuild_rows(raw, sym, hfq)
        if new is not None:
            frames.append(new.select(final.columns))
    if not frames:
        return keep
    return pl.concat([keep, *frames], how="vertical_relaxed")


# ---- 兼容旧内部辅助（保留给潜在直接调用方；主流程已不逐票使用） ----

def _fetch_hfq(client, sym: str) -> dict:
    """单票 hfq 因子 {date: factor}；失败返回 {}。"""
    try:
        pdf = client.call("stock_adj_factor_tdx", code=sym, adjust="hfq")
    except Exception as e:  # noqa: BLE001
        logger.warning("因子拉取失败 %s: %s", sym, e)
        return {}
    df = _normalize_factor_pdf(pdf, [sym])
    return _df_to_hfq_map(df).get(sym, {})


def _rebuild_symbol_qfq(store: Path, sym: str, hfq: dict) -> bool:
    """旧单票写盘接口（保留兼容；新主流程请用 _apply_rebuilds 一次写盘）。"""
    raw_file = store / "kline_daily_raw" / "_all.parquet"
    qfq_file = store / "kline_daily" / "_all.parquet"
    raw = _load(raw_file)
    new = _rebuild_rows(raw, sym, hfq)
    if new is None:
        return False
    qfq = _load(qfq_file)
    qfq = qfq.filter(pl.col("symbol") != sym)
    qfq = pl.concat([qfq, new], how="vertical_relaxed").sort(
        ["symbol", "date"])
    tmp = qfq_file.with_suffix(".parquet.tmp")
    qfq.drop("_d").write_parquet(tmp)
    os.replace(tmp, qfq_file)
    logger.info("qfq rebuilt %s", sym)
    return True
