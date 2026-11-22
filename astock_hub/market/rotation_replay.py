"""盘中板块切换·分钟回放（B2-4，对齐 TSP sector_rotation 核心口径）。

- 单股分钟涨跌幅 = close / raw 昨收 - 1；板块 = 成分股等权均值。
- 默认 5 分钟桶；rotation(t) 比较 t 与约 1 小时前桶的 top10 重合率。
- 走势线/热力图共用同一分钟矩阵；领涨易主与 0 轴事件均来自分钟序列。
数据只读盘后分钟库，不冒充盘中实时。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import time

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
MINUTE_DIR = ROOT / "data" / "backtest" / "minute"
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 300
RANK_WINDOW_MIN = 60


def _latest_day(root: Path) -> str | None:
    today = date.today().isoformat()
    days = [p.stem for p in root.glob("????-??-??.parquet") if p.stem <= today]
    return max(days) if days else None


def _elapsed(minutes: int) -> int:
    """距开盘的交易分钟（午休不计），用于找约 1 小时前的对照桶。"""
    morning_end = 11 * 60 + 30
    if minutes <= morning_end:
        return minutes - (9 * 60 + 30)
    return 120 + minutes - (13 * 60)


def _reference_index(times: list[int], index: int) -> int | None:
    if index <= 0:
        return None
    current = _elapsed(times[index])
    for back in range(index - 1, -1, -1):
        if current - _elapsed(times[back]) >= RANK_WINDOW_MIN:
            return back
    return 0  # 开盘不足一小时，与最早桶比


def _fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _aggregate(frame: pl.DataFrame, map_df: pl.DataFrame, kind: str,
               bucket_minutes: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    minute_of_day = (pl.col("time").str.slice(0, 2).cast(pl.Int32) * 60
                     + pl.col("time").str.slice(3, 2).cast(pl.Int32))
    valid = (pl.col("pre_close") > 0) & (pl.col("close") > 0)
    pct = pl.col("close") / pl.col("pre_close") - 1
    bars = (frame.filter(valid)
            .with_columns(((minute_of_day / bucket_minutes).cast(pl.Int32)
                           * bucket_minutes).alias("_bucket"), pct.alias("_pct"))
            .filter(pl.col("_pct").abs() <= 0.30))
    # 先每股每桶聚合再 join 成分，避免把全市场分钟行放大数倍。
    stocks = (bars.group_by(["_bucket", "symbol"])
              .agg(pl.col("_pct").mean().alias("_pct")))
    market = stocks.group_by("_bucket").agg(pl.col("_pct").mean().alias("market_pct")).sort("_bucket")
    members = map_df.select(["_sym_up", kind]).rename({"_sym_up": "symbol", kind: "member"})
    sectors = (stocks.join(members, on="symbol", how="inner")
               .group_by(["_bucket", "member"])
               .agg(pl.col("_pct").mean().alias("pct"), pl.len().alias("n"))
               .sort(["_bucket", "pct"], descending=[False, True]))
    return sectors, market


def build_replay(sectors: pl.DataFrame, market: pl.DataFrame,
                 bucket_minutes: int = 5, top: int = 10) -> dict:
    times = sorted(sectors["_bucket"].unique().to_list())
    per_bucket = []
    cross_events: list[dict] = []
    prev_sign: dict[str, int] = {}
    market_map = {r["_bucket"]: r["market_pct"] for r in market.iter_rows(named=True)}
    for bucket in times:
        frame = sectors.filter(pl.col("_bucket") == bucket).sort("pct", descending=True)
        names = frame["member"].to_list()
        pcts = frame["pct"].to_list()
        up = down = 0
        for name, pct in zip(names, pcts):
            sign = 1 if pct >= 0 else -1
            old = prev_sign.get(name)
            if old is not None and old != sign:
                direction = "up" if sign > 0 else "down"
                up += sign > 0
                down += sign < 0
                cross_events.append({"time": _fmt_time(bucket), "name": name,
                                     "dir": direction, "pct": pct})
            prev_sign[name] = sign
        per_bucket.append({"bucket": bucket, "names": names, "pcts": pcts,
                           "top": set(names[:10]), "leader": names[0] if names else None,
                           "leader_pct": pcts[0] if pcts else None,
                           "cross_up": up, "cross_down": down})

    timeline = []
    leader_changes = []
    prev_leader = None
    for i, item in enumerate(per_bucket):
        back = _reference_index(times, i)
        if back is not None:
            other = per_bucket[back]
            width = min(10, len(item["top"]), len(other["top"]))
            overlap = len(item["top"] & other["top"]) / width if width else 1.0
        else:
            overlap = 1.0
        timeline.append({"time": _fmt_time(item["bucket"]),
                         "rotation": round(1 - min(1.0, max(0.0, overlap)), 4),
                         "leader": item["leader"], "leader_pct": item["leader_pct"],
                         "market_pct": market_map.get(item["bucket"]),
                         "cross_up": item["cross_up"], "cross_down": item["cross_down"]})
        if item["leader"] and prev_leader and item["leader"] != prev_leader:
            leader_changes.append({"time": _fmt_time(item["bucket"]),
                                   "from": prev_leader, "to": item["leader"]})
        prev_leader = item["leader"] or prev_leader

    last = per_bucket[-1] if per_bucket else {"names": [], "pcts": []}
    display = last["names"][:top]
    matrix = []
    for name in display:
        values = []
        for item in per_bucket:
            values.append(dict(zip(item["names"], item["pcts"])).get(name))
        matrix.append({"name": name, "values": values})

    ref = _reference_index(times, len(times) - 1) if times else None
    prev_pct = dict(zip(per_bucket[ref]["names"], per_bucket[ref]["pcts"])) if ref is not None else {}
    prev_rank = {n: i + 1 for i, n in enumerate(per_bucket[ref]["names"])} if ref is not None else {}
    ranks = {n: i + 1 for i, n in enumerate(last["names"])}
    sector_rows = [{"name": n, "pct_now": last["pcts"][i],
                    "pct_prev": prev_pct.get(n), "rank_now": ranks[n],
                    "rank_prev": prev_rank.get(n),
                    "rank_change": (prev_rank[n] - ranks[n] if n in prev_rank else None)}
                   for i, n in enumerate(display)]
    return {"timeline": timeline, "series": {"sectors": display, "matrix": matrix},
            "sectors": sector_rows, "leader_changes": leader_changes,
            "cross_events": list(reversed(cross_events[-120:]))}


def rotation_replay(cfg: dict, kind: str = "concept", bucket_minutes: int = 5,
                    day: str | None = None, *, root: Path | None = None) -> dict:
    from ..store.concepts import concept_map_df
    from .mainline import ATTRIBUTE_SECTOR_BLACKLIST

    base = root or MINUTE_DIR
    target = day or _latest_day(base)
    if not target:
        return {"status": "no_data", "available": False, "reason": "no_data"}
    path = base / f"{target}.parquet"
    if not path.exists():
        return {"status": "no_data", "available": False, "date": target, "reason": "no_data"}
    key = (str(base.resolve()), kind, bucket_minutes, target)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    frame = pl.scan_parquet(path).select(["symbol", "time", "close", "pre_close"]).collect()
    map_df, _ = concept_map_df(cfg, kind)
    if kind == "concept":
        map_df = map_df.filter(
            ~pl.col(kind).is_in(ATTRIBUTE_SECTOR_BLACKLIST),
            ~pl.col(kind).str.contains("年报预|季报预|中报预|业绩预"))
    # 与 TSP 自动榜口径一致：成员太少不具备板块意义，>300 的超大桶没有轮动辨识度。
    counts = map_df.group_by(kind).len().rename({"len": "_members"})
    map_df = map_df.join(counts, on=kind).filter(
        (pl.col("_members") >= 4) & (pl.col("_members") <= 300)).drop("_members")
    sectors, market = _aggregate(frame, map_df, kind, bucket_minutes)
    if sectors.is_empty():
        return {"status": "no_data", "available": False, "date": target, "reason": "no_member_bars"}
    result = build_replay(sectors, market, bucket_minutes)
    result.update({"status": "ok", "available": True, "date": target, "kind": kind,
                   "bucket_minutes": bucket_minutes, "basis": "raw_pre_close",
                   "note": f"{target} 盘后分钟回放；{bucket_minutes}分钟桶；换血率与约1小时前前十比较"})
    _CACHE[key] = (time.monotonic(), result)
    return result
