"""日期分区 Parquet 仓库管理。

参考 tickflow-stock-panel 的设计：
- 按日期分区：kline_daily/date=YYYY-MM-DD/part.parquet
- Polars scan_parquet 读取，无 DuckDB 依赖
- 支持增量更新
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import polars as pl

from .. import models
from ..adapters.hithink import HithinkAdapter

logger = logging.getLogger(__name__)

# 仓库根目录（锚定包根目录，不依赖进程 cwd）
PARQUET_STORE = Path(__file__).resolve().parents[2] / "data" / "market"


def _store_dir(cfg: dict) -> Path:
    """返回日期分区 parquet 仓库根目录"""
    base = Path(cfg.get("hithink_data", {}).get("cache_dir", "data/hithink_cache"))
    store = base.parent / "market"
    store.mkdir(parents=True, exist_ok=True)
    return store


def _adapter(cfg: dict) -> HithinkAdapter:
    from ..adapters.hithink import HithinkAdapter
    return HithinkAdapter(cfg.get("hithink_data", {}) or cfg.get("hithink", {}))


def _convert_volume_to_lots(path: Path) -> None:
    """hithink 面板 volume 单位是股，统一换算成手（与通达信盘后包/腾讯口径一致）。

    历史教训（2026-09-30）：init_store 早年直写 hithink 面板（股），sync_store
    追加通达信数据（手），仓库单位断档导致全市场量比 ~0.01、所有带量比条件的
    策略误报 0。新写入路径必须全部过本函数。
    """
    if not path.exists():
        return
    df = pl.read_parquet(path)
    if "volume" in df.columns:
        df = df.with_columns((pl.col("volume") / 100).alias("volume"))
        df.write_parquet(path)


def init_store(cfg: dict, force: bool = False) -> dict:
    """初始化日期分区仓库：从 hithink 导出全市场历史数据。

    只需运行一次（首次安装或数据丢失时），后续用 sync_store 增量更新。
    """
    store = _store_dir(cfg)
    adapter = _adapter(cfg)

    # 创建目录结构
    for subdir in ["kline_daily", "kline_daily_raw", "instruments", "calendar"]:
        (store / subdir).mkdir(parents=True, exist_ok=True)

    # 导出全市场历史面板（前复权 + 不复权）
    # 范围：2020-01-01 至今（足够覆盖所有分析需求）
    start = "2020-01-01"
    from datetime import date as _date
    end = _date.today().isoformat()

    logger.info("初始化仓库：导出 %s ~ %s 全市场数据...", start, end)

    # 前复权面板
    qfq_path = store / "kline_daily" / "_all.parquet"
    if force or not qfq_path.exists():
        try:
            res = adapter.data_panel(start, end, str(qfq_path), timeout=1800)
            if res.status != models.OK:
                raise RuntimeError(f"data_panel 导出失败：{res.status}")
            _convert_volume_to_lots(qfq_path)
            logger.info("前复权面板导出成功: %s", qfq_path)
        except Exception as e:
            logger.warning("前复权面板导出失败: %s", e)
            return {"ok": False, "error": str(e), "source": ""}

    # 不复权面板（用于涨跌停判定）
    raw_path = store / "kline_daily_raw" / "_all.parquet"
    if force or not raw_path.exists():
        try:
            res = adapter.data_panel(start, end, str(raw_path), timeout=1800)
            if res.status != models.OK:
                raise RuntimeError(f"data_panel raw 导出失败：{res.status}")
            _convert_volume_to_lots(raw_path)
            logger.info("不复权面板导出成功: %s", raw_path)
        except Exception as e:
            logger.warning("不复权面板导出失败: %s", e)

    # 按日期分区写入（可选，优化查询性能）
    _partition_by_date(store, qfq_path, "kline_daily")
    _partition_by_date(store, raw_path, "kline_daily_raw")

    return {"ok": True, "store": str(store), "start": start, "end": end}


def _sync_now():
    from datetime import datetime
    return datetime.now()


def _tdx_zip_fetch(day: str):
    from ..adapters.astock_data import tdx_daily_package
    return tdx_daily_package(day)


def _tdx_proto_fetch(day: str):
    from ..adapters.tdx_protocol import tdx_full_market_day
    return tdx_full_market_day(day)


def _refresh_qfq_quiet(cfg: dict) -> dict:
    """提前返回分支的 qfq 补跑：失败只警告不阻断 raw 同步返回。

    sync_store 的"已是最新/无新数据"分支此前直接 return，raw 一旦写入当日，
    qfq 断更就永远没人补（2026-11-19 假跌停事故）。这里统一补跑并容忍失败；
    refresh_qfq_daily 内部幂等（qfq 已最新则秒回，断更则补跑）。
    """
    try:
        from .qfq_refresh import refresh_qfq_daily
        return refresh_qfq_daily(cfg)
    except Exception as e:  # noqa: BLE001 — qfq 失败不阻断 raw 同步
        logger.warning("qfq 补跑失败（raw 同步已就绪）: %s", e)
        return {"ok": False, "error": str(e)[:200]}


def sync_store(cfg: dict) -> dict:
    """增量同步：使用通达信盘后包获取新交易日数据。"""
    store = _store_dir(cfg)
    raw_file = store / "kline_daily_raw" / "_all.parquet"

    # 获取当前最新日期
    if raw_file.exists():
        df = pl.read_parquet(raw_file)
        last_date = str(df["date"].max())[:10]
    else:
        last_date = None

    if not last_date:
        return init_store(cfg)

    # 从最后日期的下一天开始
    from datetime import datetime, timedelta
    start_date = datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)
    end_date = datetime.now().date()

    if start_date.date() > end_date:
        # raw 已最新，但 qfq 可能断更（refresh_qfq_daily 此前崩溃未补，2026-11-19
        # 事故）——提前返回前也必须补跑；其内部幂等（qfq 已最新则秒回，断更则补）。
        # 失败容忍：qfq 补跑失败不阻断 raw 同步的"已最新"返回。
        return {"ok": True, "message": "已是最新", "last_date": last_date,
                "source": "无新数据", "qfq": _refresh_qfq_quiet(cfg)}

    from ..adapters.tdx_protocol import protocol_eligible

    all_rows = []
    sources = set()
    current = start_date.date()
    now = _sync_now()
    use_proto_cfg = (cfg.get("data_source") or {}).get("tdx_sync_protocol", True)
    while current <= end_date:
        day_str = current.strftime("%Y-%m-%d")
        use_proto = (use_proto_cfg and day_str == now.strftime("%Y-%m-%d")
                     and protocol_eligible(day_str, now, cfg))
        try:
            if use_proto:
                try:
                    df = _tdx_proto_fetch(day_str)
                    sources.add("通达信协议")
                except Exception as e:  # noqa: BLE001 — 协议失败必降级 zip，链不差于现状
                    logger.warning("协议取 %s 失败降级 zip: %s", day_str, e)
                    df = _tdx_zip_fetch(day_str)
                    sources.add("盘后包(协议降级)")
            else:
                df = _tdx_zip_fetch(day_str)
                sources.add("盘后包")
            all_rows.extend(df.to_dicts())
        except ValueError:
            pass  # 非交易日
        except Exception as e:
            logger.warning("取 %s 失败: %s", day_str, e)
        current += timedelta(days=1)

    if not all_rows:
        return {"ok": True, "message": "无新数据", "last_date": last_date,
                "source": "无新数据", "qfq": _refresh_qfq_quiet(cfg)}

    # 合并并写入缓存
    new_df = pl.DataFrame(all_rows)
    # 转换列名：thscode → symbol
    if "thscode" in new_df.columns:
        new_df = new_df.rename({"thscode": "symbol"})
    # 统一 date 格式
    if "date" in new_df.columns and new_df["date"].dtype == pl.Utf8:
        new_df = new_df.with_columns(pl.col("date").str.to_date("%Y-%m-%d"))
    # volume 从股转手
    if "volume" in new_df.columns:
        new_df = new_df.with_columns((pl.col("volume") / 100).alias("volume"))

    if raw_file.exists():
        existing = pl.read_parquet(raw_file)
        # 统一列名：thscode → symbol
        if "thscode" in existing.columns and "symbol" not in existing.columns:
            existing = existing.rename({"thscode": "symbol"})
        if "thscode" in new_df.columns and "symbol" not in new_df.columns:
            new_df = new_df.rename({"thscode": "symbol"})
        new_df = pl.concat([existing, new_df]).unique(subset=["symbol", "date"], keep="last")

    store.mkdir(parents=True, exist_ok=True)
    # 原子写：先写临时文件再 rename，消掉协议腿 6 分钟窗口的并发撕裂写
    tmp_file = raw_file.with_suffix(".parquet.tmp")
    new_df.write_parquet(tmp_file)
    os.replace(tmp_file, raw_file)

    # qfq 增量（2026-11-12 qfq 立项配套）：raw 更新后前复权库跟上
    from .qfq_refresh import refresh_qfq_daily
    qfq_res = refresh_qfq_daily(cfg)
    if not qfq_res.get("ok"):
        logger.warning("qfq incremental failed (raw updated ok): %s", qfq_res)

    return {"ok": True, "synced_to": end_date, "rows_added": len(all_rows),
            "source": "+".join(sorted(sources)) if sources else "无新数据",
            "qfq": qfq_res}


def _get_last_date(store: Path) -> str | None:
    """从仓库获取最新日期"""
    qfq_path = store / "kline_daily" / "_all.parquet"
    if not qfq_path.exists():
        return None
    try:
        df = pl.read_parquet(qfq_path, columns=["date"])
        return str(df["date"].max())[:10]
    except Exception:
        return None


def _partition_by_date(store: Path, source: Path, subdir: str):
    """将单个大文件按日期分区写入"""
    if not source.exists():
        return

    df = pl.read_parquet(source)
    if df.is_empty():
        return

    out_dir = store / subdir
    for date_val, group in df.group_by("date"):
        date_str = str(date_val[0])[:10]
        date_dir = out_dir / f"date={date_str}"
        date_dir.mkdir(parents=True, exist_ok=True)
        out_file = date_dir / "part.parquet"
        group.write_parquet(out_file)

    logger.info("按日期分区写入完成：%s，共 %d 个分区", subdir, df["date"].n_unique())


def _filter_date(df: pl.DataFrame, start_date, end_date) -> pl.DataFrame:
    """date 列 dtype 兼容：Date 列用 date 比较，String 列用 ISO 串比较。"""
    if df.is_empty():
        return df
    if df.schema.get("date") in (pl.Date, pl.Datetime):
        return df.filter((pl.col("date") >= start_date)
                         & (pl.col("date") <= end_date))
    s = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)
    e = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)
    col = pl.col("date").cast(pl.Utf8).str.slice(0, 10)
    return df.filter((col >= s) & (col <= e))


def read_market(store_dir: Path, start: str, end: str,
                *, with_raw: bool = True) -> pl.DataFrame:
    """从日期分区仓库读取全市场数据（前复权 + 可选原始价）"""
    from datetime import date as _date

    start_date = _date.fromisoformat(start) if isinstance(start, str) else start
    end_date = _date.fromisoformat(end) if isinstance(end, str) else end

    # 读取原始价数据（优先，因为通达信盘后包更新到这里）
    raw_file = store_dir / "kline_daily_raw" / "_all.parquet"
    if not raw_file.exists():
        raise RuntimeError(f"仓库数据不存在：{store_dir / 'kline_daily_raw'}")

    raw = pl.read_parquet(raw_file)
    raw = _filter_date(raw, start_date, end_date)
    if "thscode" in raw.columns:
        raw = raw.rename({"thscode": "symbol"})

    if raw.is_empty():
        return pl.DataFrame()

    # 只保留需要的列并重命名
    raw_cols = [c for c in ("symbol", "date", "open", "high", "low", "close", "volume", "amount") if c in raw.columns]
    raw = raw.select(raw_cols).rename({
        "open": "raw_open", "high": "raw_high",
        "low": "raw_low", "close": "raw_close"
    })

    if not with_raw:
        # 注意：此分支返回**不复权价**（且无 raw_* 列）——与 market_data
        # _export_panel 的 with_raw=False（返回前复权）语义相反，调用方
        # 按名取列时别混淆；要前复权用 with_raw=True 取 open/close 列。
        return raw.rename({
            "raw_open": "open", "raw_high": "high",
            "raw_low": "low", "raw_close": "close"
        })

    # 前复权（2026-11-12 qfq 立项修复）：kline_daily/_all.parquet 由
    # scripts/build_kline_qfq.py 生成（raw × hfq 锚 2020-01-02）。
    # raw 为基 left join：qfq 提供前复权 OHLC，缺行/缺因子票 coalesce
    # 回落 raw（保守回落好过 null 断链）。
    qfq_file = store_dir / "kline_daily" / "_all.parquet"
    if qfq_file.exists():
        qfq_all = pl.read_parquet(qfq_file)
        # qfq 断更检测：请求 end 超出 qfq 覆盖（如 refresh_qfq_daily 失败，
        # raw 已到 11-19、qfq 停在 11-18）时，raw 为基 left join 会让 close 列
        # 前一天复权、后一天原始——compute_limit_signals 把复权因子突变误判为
        # 除权，用前复权昨收判涨跌停 → 全市场假跌停（2026-11-19 对账 FAIL）。
        # 此时整段降级为统一原始价（交易所口径），qfq 补回后自动恢复复权。
        qfq_last = qfq_all["date"].max()
        if qfq_last is not None and qfq_last >= end_date:
            qfq = _filter_date(qfq_all, start_date, end_date)
            if "thscode" in qfq.columns and "symbol" not in qfq.columns:
                qfq = qfq.rename({"thscode": "symbol"})
            if not qfq.is_empty():
                qfq = qfq.select(["symbol", "date", "open", "high", "low", "close"])
                result = raw.join(qfq, on=["symbol", "date"], how="left")
                result = result.with_columns([
                    pl.coalesce(["open", "raw_open"]).alias("open"),
                    pl.coalesce(["high", "raw_high"]).alias("high"),
                    pl.coalesce(["low", "raw_low"]).alias("low"),
                    pl.coalesce(["close", "raw_close"]).alias("close"),
                ]).select(["symbol", "date", "open", "high", "low", "close",
                          "volume", "amount", "raw_open", "raw_high",
                          "raw_low", "raw_close"])
                return result

    # 无 qfq 文件 / qfq 断更未覆盖请求 end：close==raw_close（统一原始价，
    # 交易所口径——涨跌停判定本就不该用复权价；qfq 缺失降级，2026-11-12 前旧行为）
    result = raw.rename({
        "raw_open": "open", "raw_high": "high",
        "raw_low": "low", "raw_close": "close"
    })
    result = result.with_columns([
        pl.col("open").alias("raw_open"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
        pl.col("close").alias("raw_close"),
    ])
    return result

