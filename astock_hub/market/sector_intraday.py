"""板块等权分时 vs 全市场（B2-5，盘后分钟库回放）。

每只股票先按 raw 昨收计算分钟涨跌幅，再在板块内等权平均；全市场使用同一
算法。缺昨收/非正价格的行不参与，覆盖率如实返回。数据来自回测分钟库，
不代表盘中实时；调用方必须展示 as_of 与覆盖数。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import time

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
MINUTE_DIR = ROOT / "data" / "backtest" / "minute"
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 60


def _latest_day(root: Path) -> str | None:
    today = date.today().isoformat()
    days = [p.stem for p in root.glob("????-??-??.parquet") if p.stem <= today]
    return max(days) if days else None


def _aggregate(df: pl.DataFrame, symbols: list[str], day: str,
               member_total: int) -> dict:
    if df.is_empty() or not symbols:
        return {"available": False, "as_of": day, "reason": "no_data",
                "member_total": member_total, "points": []}

    valid = (pl.col("pre_close") > 0) & (pl.col("close") > 0)
    sector = pl.col("symbol").is_in(symbols)
    pct = pl.col("close") / pl.col("pre_close") - 1
    out = (df.lazy().group_by("time").agg([
        pl.when(sector & valid).then(pct).otherwise(None).mean().alias("sector"),
        pl.when(valid).then(pct).otherwise(None).mean().alias("market"),
    ]).sort("time").collect())
    covered = df.filter(sector & valid)["symbol"].n_unique()
    market_count = df.filter(valid)["symbol"].n_unique()
    points = [
        {"time": r["time"], "sector": r["sector"], "market": r["market"]}
        for r in out.iter_rows(named=True)
        if r["sector"] is not None or r["market"] is not None
    ]
    return {
        "available": bool(points) and covered > 0,
        "as_of": day,
        "reason": None if points and covered else "no_data",
        "member_total": member_total,
        "covered": covered,
        "market_count": market_count,
        "points": points,
    }


def sector_intraday(cfg: dict, kind: str, name: str, day: str | None = None,
                    *, root: Path | None = None) -> dict:
    """返回板块与全市场等权分时；无当日档时默认取最近有档交易日。"""
    from ..store.concepts import concept_map_df

    base = root or MINUTE_DIR
    target = day or _latest_day(base)
    if not target:
        return {"available": False, "reason": "no_data", "points": []}
    path = base / f"{target}.parquet"
    if not path.exists():
        return {"available": False, "as_of": target, "reason": "no_data", "points": []}

    key = (str(base.resolve()), kind, name, target)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    map_df, _ = concept_map_df(cfg, kind)
    symbols = map_df.filter(pl.col(kind) == name)["_sym_up"].unique().to_list()
    df = (pl.scan_parquet(path)
          .select(["symbol", "time", "close", "pre_close"])
          .collect())
    result = _aggregate(df, symbols, target, len(symbols))
    _CACHE[key] = (time.monotonic(), result)
    return result
