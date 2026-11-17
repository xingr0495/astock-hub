"""指标计算的取数层：把 hithink 日线历史拼成 polars DataFrame。

**单位口径必须与 tickflow 对齐，否则指标数值对不上**：
- hithink 的 `volume` 单位是**股**，tickflow 的 `volume` 是**手**（1 手 = 100 股），
  这里统一换算成手；`vol_ratio_5d` 这类比值不受影响，但成交量列本身要与下游一致。
- hithink 的 `turnover` 字段是成交额（元）→ 映射成 `amount`。

涨跌停信号需要**原始价**：前复权序列会把除权日的涨跌停基准抹平，因此
`with_raw=True` 时再拉一次 `--adjust none` 的序列，按日期对齐成
`raw_close / raw_high / raw_low`（tickflow 的 enriched 表里存的也是这套）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from .. import models
from ..adapters.hithink import HithinkAdapter

_CST = timezone(timedelta(hours=8))
_SHARES_PER_LOT = 100  # 1 手 = 100 股


def _rows_to_frame(result: models.DimensionResult, ts_code: str, *,
                   prefix: str = "") -> pl.DataFrame:
    """把 hithink history 的 item 列表转成 polars 帧（列名与 tickflow 对齐）。"""
    items = (result.data or {}).get("item") or (result.data or {}).get("rows") or []
    if not items:
        raise ValueError(f"{ts_code} 没有取到日线数据（{result.status}）")
    rows = []
    for it in items:
        ms = it.get("date_ms")
        if ms is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(int(ms) / 1000, tz=_CST).date(),
            f"{prefix}open": it.get("open_price"),
            f"{prefix}high": it.get("high_price"),
            f"{prefix}low": it.get("low_price"),
            f"{prefix}close": it.get("close_price"),
            **({"volume": (it.get("volume") or 0) / _SHARES_PER_LOT,
                "amount": it.get("turnover")} if not prefix else {}),
        })
    if not rows:
        raise ValueError(f"{ts_code} 的日线数据没有可用日期")
    return pl.DataFrame(rows).with_columns(pl.lit(ts_code).alias("symbol"))


def daily_frame(ht: HithinkAdapter, ts_code: str, *, days: int = 400,
                with_raw: bool = False) -> pl.DataFrame:
    """取单只股票的日线（前复权 + 可选原始价），返回 tickflow 同构的 polars 帧。

    `days` 是交易日数；为了让 MA60/年化波动等窗口在开头就算得出来，调用方应
    按"展示行数 + 预热行数"给足（默认 400 已含充足预热）。
    """
    adj = ht.history(ts_code, days=days, tail=days, adjust="forward")
    if adj.status != models.OK:
        hint = (adj.error or {}).get("hint") or ""
        raise ValueError(f"取 {ts_code} 前复权日线失败：{adj.status} {hint}".strip())

    df = _rows_to_frame(adj, ts_code)

    if with_raw:
        raw = ht.history(ts_code, days=days, tail=days, adjust="none")
        if raw.status == models.OK:
            raw_df = _rows_to_frame(raw, ts_code, prefix="raw_")
            # raw 帧里没有 volume/amount（且只需价格），按日期左连接
            df = df.join(
                raw_df.select(["symbol", "date", "raw_open", "raw_high", "raw_low", "raw_close"]),
                on=["symbol", "date"], how="left",
            )
    return df.sort(["symbol", "date"])


# ---------------------------------------------------------------- a-stock-data（腾讯K线）

def _tencent_kline_frame(ts_code: str, days: int, *, adjust: str | None,
                         prefix: str = "") -> pl.DataFrame:
    """腾讯 K 线（a-stock-data）→ tickflow 同构帧。

    口径：volume 单位是**手**（与管道一致，无需换算）；响应没有成交额字段，
    amount 用 `close × volume × 100` 估算（仅用于展示/成交密集区，不影响均线类指标）。
    注意腾讯日K会带上**当日未成型的盘中 bar**（交易日盘中）。
    """
    from ..adapters.astock_data import tencent_kline

    df = tencent_kline(ts_code, adjust=adjust, count=min(max(days, 20), 640))
    if df.is_empty():
        raise ValueError(f"{ts_code} 腾讯K线没有取到数据")

    recs = []
    for row in df.iter_rows(named=True):
        d = datetime.strptime(str(row["date"])[:10], "%Y-%m-%d").date()
        rec = {
            "date": d,
            f"{prefix}open": row.get("open"),
            f"{prefix}high": row.get("high"),
            f"{prefix}low": row.get("low"),
            f"{prefix}close": row.get("close"),
        }
        if not prefix:
            vol = row.get("volume") or 0
            rec["volume"] = vol
            rec["amount"] = (row.get("close") or 0) * vol * _SHARES_PER_LOT
        recs.append(rec)
    return pl.DataFrame(recs).with_columns(pl.lit(ts_code).alias("symbol"))


def daily_frame_astockdata(ts_code: str, *, days: int = 400,
                           with_raw: bool = False) -> pl.DataFrame:
    """a-stock-data（腾讯K线）取单股日线（前复权 + 可选原始价），与 `daily_frame` 同构。"""
    df = _tencent_kline_frame(ts_code, days, adjust="qfq")
    if with_raw:
        try:
            raw_df = _tencent_kline_frame(ts_code, days, adjust="", prefix="raw_")
            df = df.join(
                raw_df.select(["symbol", "date", "raw_open", "raw_high", "raw_low", "raw_close"]),
                on=["symbol", "date"], how="left",
            )
        except Exception:  # noqa: BLE001 — 原始价拉不到就缺省（涨跌停信号退化为无）
            pass
    return df.sort(["symbol", "date"])


# ---------------------------------------------------------------- axdata（通达信协议栈降级源）

def _axdata_kline_frame(ts_code: str, days: int, *, adjust: str = "qfq",
                        prefix: str = "") -> pl.DataFrame:
    """axdata（通达信协议栈）取单股日线 → tickflow 同构帧。

    作为腾讯K线的降级源：mootdx 已停止维护且实测取不到数（2026-09-30 空返回），
    axdata 是同协议栈的活跃替代。口径：volume 已是**手**（与管道一致，无需换算）；
    amount 直接用其成交额（元）。adjust 支持 qfq/hfq/''（不复权）。
    """
    from axdata import AxDataClient

    client = AxDataClient()
    pdf = client.call("stock_kline_daily_tdx", code=ts_code, adjust=adjust)
    if pdf is None or len(pdf) == 0:
        raise ValueError(f"{ts_code} axdata 返回空")

    recs = []
    for row in pdf.tail(days).to_dict("records"):
        d = datetime.strptime(str(row.get("trade_time") or "")[:10], "%Y-%m-%d").date()
        rec = {
            f"{prefix}date": d,
            f"{prefix}open": float(row["open"]),
            f"{prefix}high": float(row["high"]),
            f"{prefix}low": float(row["low"]),
            f"{prefix}close": float(row["close"]),
        }
        if not prefix:
            rec["volume"] = float(row["volume"])
            rec["amount"] = float(row["amount"])
        recs.append(rec)
    if not recs:
        raise ValueError(f"{ts_code} axdata 无有效行")
    return pl.DataFrame(recs).with_columns(pl.lit(ts_code).alias("symbol"))


def daily_frame_axdata(ts_code: str, *, days: int = 400,
                       with_raw: bool = False) -> pl.DataFrame:
    """axdata 取单股日线（前复权 + 可选原始价），与 `daily_frame_astockdata` 同构。"""
    df = _axdata_kline_frame(ts_code, days, adjust="qfq")
    if with_raw:
        try:
            raw_df = _axdata_kline_frame(ts_code, days, adjust="", prefix="raw_")
            df = df.join(
                raw_df.select(["symbol", "date", "raw_open", "raw_high", "raw_low", "raw_close"]),
                on=["symbol", "date"], how="left",
            )
        except Exception:  # noqa: BLE001 — 原始价拉不到就缺省
            pass
    return df.sort(["symbol", "date"])


def indicators_for_symbol_axdata(ts_code: str, *, days: int = 400,
                                 name: str | None = None,
                                 with_raw: bool = False) -> pl.DataFrame:
    """axdata 版「取数 + 过滤停牌 + 算全套指标」（腾讯K线降级用）。"""
    from .pipeline import compute_all, filter_halt_days

    df = daily_frame_axdata(ts_code, days=days, with_raw=with_raw)
    df = filter_halt_days(df)
    return compute_all(df, name=name)


def indicators_for_symbol_astockdata(ts_code: str, *, days: int = 400,
                                     name: str | None = None,
                                     with_raw: bool = False) -> pl.DataFrame:
    """a-stock-data 版「取数 + 过滤停牌 + 算全套指标」（与 indicators_for_symbol 平行）。"""
    from .pipeline import compute_all, filter_halt_days

    df = daily_frame_astockdata(ts_code, days=days, with_raw=with_raw)
    df = filter_halt_days(df)
    return compute_all(df, name=name)


def frame_from_kline_rows(rows: list[dict], symbol: str) -> pl.DataFrame:
    """把"已是行字典"的日线（如 tickflow 批量K线返回的 rows）转成 polars 帧。

    字段按 tickflow 口径（open/high/low/close/volume/amount/date）；日期接受
    'YYYY-MM-DD' 字符串或 date 对象。用途之一是让 pool.py 不再自己手写均线公式。
    """
    recs = []
    for r in rows:
        d = r.get("date") or r.get("trade_date")
        if d is None:
            continue
        if isinstance(d, str):
            d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        recs.append({
            "date": d,
            "open": r.get("open"), "high": r.get("high"),
            "low": r.get("low"), "close": r.get("close"),
            "volume": r.get("volume"), "amount": r.get("amount"),
        })
    if not recs:
        raise ValueError(f"{symbol} 没有可用的日K行")
    return (pl.DataFrame(recs)
            .with_columns(pl.lit(symbol).alias("symbol"))
            .sort(["symbol", "date"]))


def indicators_for_symbol(ht: HithinkAdapter, ts_code: str, *, days: int = 400,
                          name: str | None = None, with_raw: bool = False) -> pl.DataFrame:
    """取数 + 过滤停牌日 + 算全套指标与信号（webapi / pool 的一行入口）。"""
    from .pipeline import compute_all, filter_halt_days

    df = daily_frame(ht, ts_code, days=days, with_raw=with_raw)
    df = filter_halt_days(df)
    return compute_all(df, name=name)
