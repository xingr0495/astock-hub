"""板块涨幅轮动矩阵（从 tickflow `backend/app/services/rps_rotation.py` 移植）。

输出"每天各自把所有概念按当天平均涨幅从高到低排序"的矩阵，供前端"概念轮动"使用。

移植边界：
- 聚合与排序逻辑逐字照搬（简单平均口径、每日独立排序）。
- tickflow 的 repo、内存缓存、结果 TTL 缓存不搬：缓存交给调用层（store/service）。
- 本模块只吃两个 DataFrame：`daily`（date, symbol, change_pct，小数口径）与 `map_df`
  （_sym_up → 成员，来自 store.concepts）。

差异：同花顺行业名称扁平、没有"一级-二级-三级"路径，因此 tickflow 的行业 `level`
参数不支持（按全名聚合）。
"""

from __future__ import annotations

import logging

import polars as pl

logger = logging.getLogger(__name__)


def rps_rotation(daily: pl.DataFrame, map_df: pl.DataFrame, kind: str = "concept"
                 ) -> dict:
    """构建板块涨幅轮动矩阵。

    返回 {"dates": [最新 → 最早], "columns": {date: [[成员, 涨幅], ...](高→低)},
    "concept_count": 去重成员总数}。涨幅是小数（0.0522 = +5.22%）。
    """
    if daily.is_empty() or map_df.is_empty():
        return {"dates": [], "columns": {}, "concept_count": 0}
    if kind not in map_df.columns:
        raise ValueError(f"map_df 里没有成员列 {kind!r}：{map_df.columns}")
    required = {"date", "symbol", "change_pct"}
    if not required.issubset(daily.columns):
        raise ValueError(f"daily 缺列：{sorted(required - set(daily.columns))}")

    member_count = map_df[kind].n_unique()
    df = daily.with_columns(pl.col("symbol").str.to_uppercase().alias("_sym_up"))
    joined = df.join(map_df, on="_sym_up", how="inner").drop("_sym_up")
    if joined.is_empty():
        return {"dates": [], "columns": {}, "concept_count": 0}

    joined = joined.rename({kind: "member"})
    # 按 (日, 板块) 简单平均
    agg = joined.group_by(["date", "member"]).agg(
        pl.col("change_pct").mean().alias("avg_pct"))
    # 停牌/无行情的成员日丢掉
    agg = agg.filter(pl.col("avg_pct").is_not_null()
                     & pl.col("avg_pct").is_not_nan())

    # 每日内部按涨幅降序，再按日收成并行 list
    agg = agg.sort(["date", "avg_pct"], descending=[False, True])
    grouped = agg.group_by("date", maintain_order=True).agg(
        pl.col("member"), pl.col("avg_pct"))
    grouped = grouped.sort("date", descending=True)  # 最新在前

    columns: dict[str, list[list]] = {}
    dates_out: list[str] = []
    for row in grouped.iter_rows(named=True):
        d = str(row["date"])
        dates_out.append(d)
        columns[d] = [[m, float(v)] for m, v in zip(row["member"], row["avg_pct"])]

    return {"dates": dates_out, "columns": columns,
            "concept_count": member_count}


def slice_days(full: dict, days: int) -> dict:
    """从全量矩阵里取最近 N 天（最新在前，所以截前 N 个）。"""
    dates = full["dates"]
    if len(dates) <= days:
        return full
    keep = dates[:days]
    return {"dates": keep,
            "columns": {d: full["columns"][d] for d in keep},
            "concept_count": full["concept_count"]}
