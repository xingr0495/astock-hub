"""市场环境(regime)计算 —— 纯函数模块（从 tickflow 逐字移植）。

职责: 从已算好的 enriched 数据(含信号列)按日聚合环境指标, 用规则引擎分类离散状态。
不重算指标(不走 compute_indicators), 不依赖外部 quote/depth service。

五档 state（综合市场宽度分 0-100）：
  strong / lean_strong / range / lean_weak / weak
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

from .phase import (
    with_prev_consecutive,
    ladder_daily_aggs,
    ladder_promo_aggs,
    finalize_ladder_row,
)

logger = logging.getLogger(__name__)

# ───────────────── 状态分类阈值 ─────────────────
STATE_STRONG = 70       # >= 强势
STATE_LEAN_STRONG = 55  # 55-70 偏强
STATE_RANGE = 45        # 45-55 震荡
STATE_LEAN_WEAK = 30    # 30-45 偏弱
STATE_WEAK = "weak"     # <30 弱势
STATE_UNAVAILABLE = "unavailable"

STATE_LABELS = {
    "strong": "强势",
    "lean_strong": "偏强",
    "range": "震荡",
    "lean_weak": "偏弱",
    "weak": "弱势",
}


def _score(value: float, low: float, high: float) -> float:
    """归一化: value 在 [low, high] 线性映射到 [0, 100], 钳制边界。"""
    if high <= low:
        return 50.0
    return float(max(0, min(100, round((value - low) / (high - low) * 100))))


def _compute_subscores(metrics: dict) -> dict:
    """计算 6 个子维度分 + 综合分(未取整)，与 tickflow 对齐。

    6维度：index(指数) / profit(赚钱) / money(量能) / speculation(投机) / resilience(抗跌) / mainline(主线)
    """
    # 指数维度
    index = _score(metrics.get("index_pct", 0) * 100, -2.5, 2.5)

    # 赚钱维度
    profit = (
        _score(metrics.get("up_pct", 50), 20, 80) * 0.45
        + _score(metrics.get("avg_pct", 0) * 100, -2, 2) * 0.25
        + _score(metrics.get("median_pct", 0) * 100, -2, 2) * 0.20
        + _score(metrics.get("strong_diff_pct", 0), -8, 8) * 0.10
    )

    # 量能维度
    money = (
        _score(metrics.get("vol_ratio", 0.5), 0.6, 1.8) * 0.70
        + _score(metrics.get("high_vol_ratio", 0), 2, 12) * 0.30
    )

    # 投机维度
    speculation = (
        _score(metrics.get("limit_up", 0), 5, 90) * 0.25
        + _score((metrics.get("seal_rate", 0.5) or 0.5) * 100, 30, 85) * 0.35
        + _score(metrics.get("max_consecutive", 0), 1, 8) * 0.25
        + _score(metrics.get("consec_ge2", 0), 0, 30) * 0.15
    )

    # 抗跌维度
    resilience = 100 - (
        _score(metrics.get("down_pct", 50), 20, 80) * 0.55
        + _score(metrics.get("strong_down_pct", 0), 1, 12) * 0.45
    )

    # 主线维度
    mainline = _score(metrics.get("mainline_avg", 0) * 100, -0.5, 3) * 0.65 + \
               _score(metrics.get("mainline_cover_pct", 0), 1, 12) * 0.35

    score = (index + profit + money + speculation + resilience + mainline) / 6
    return {
        "index": index, "profit": profit, "money": money,
        "speculation": speculation, "resilience": resilience, "mainline": mainline,
        "score": max(0, min(100, score)),
    }


def classify_state(metrics: dict) -> tuple[str, int]:
    """规则引擎: 6 维指标 → 离散状态 + 综合分(0-100)，与 tickflow 对齐。"""
    sub = _compute_subscores(metrics)
    score = max(0, min(100, round(sub["score"])))
    if score >= STATE_STRONG:
        state = "strong"
    elif score >= STATE_LEAN_STRONG:
        state = "lean_strong"
    elif score >= STATE_RANGE:
        state = "range"
    elif score >= STATE_LEAN_WEAK:
        state = "lean_weak"
    else:
        state = STATE_WEAK
    return state, score


def run_regime(enriched: pl.DataFrame, index_pct_map: dict | None = None) -> pl.DataFrame:
    """对外便捷入口：等价于 `_aggregate_daily`。"""
    return _aggregate_daily(enriched, index_pct_map or {})


def _aggregate_daily(df: pl.DataFrame, index_pct_map: dict | None = None) -> pl.DataFrame:
    """对多日多 symbol 的 enriched DataFrame 按 date 聚合环境指标。"""
    index_pct_map = index_pct_map or {}
    if df is None or df.is_empty():
        return pl.DataFrame()
    if "date" not in df.columns or "symbol" not in df.columns:
        raise ValueError("enriched 帧缺少 date/symbol 列")

    if "consecutive_limit_ups" in df.columns:
        df = with_prev_consecutive(df)

    has_change = "change_pct" in df.columns
    has_limit_up = "signal_limit_up" in df.columns
    has_limit_down = "signal_limit_down" in df.columns
    has_broken = "signal_broken_limit_up" in df.columns
    has_consec = "consecutive_limit_ups" in df.columns
    has_ma20 = "ma20" in df.columns and "close" in df.columns

    base = [pl.len().alias("total_count")]
    if has_change:
        base += [
            pl.col("change_pct").gt(0).sum().alias("up_count"),
            pl.col("change_pct").lt(0).sum().alias("down_count"),
            pl.col("change_pct").mean().alias("avg_pct"),
            pl.col("change_pct").median().alias("median_pct"),
            pl.col("change_pct").ge(0.03).sum().alias("strong_up_count"),
            pl.col("change_pct").le(-0.03).sum().alias("strong_down_count"),
        ]
    if has_limit_up:
        base += [pl.col("signal_limit_up").cast(pl.Boolean).sum().alias("limit_up")]
    if has_limit_down:
        base += [pl.col("signal_limit_down").cast(pl.Boolean).sum().alias("limit_down")]
    if has_broken:
        base += [pl.col("signal_broken_limit_up").cast(pl.Boolean).sum().alias("broken_limit")]
    if has_consec:
        base += [pl.col("consecutive_limit_ups").max().alias("max_consecutive")]
    if "amount" in df.columns:
        base += [
            pl.col("amount").sum().alias("total_amount"),
            pl.col("amount").mean().alias("avg_amount"),
        ]
    if "vol_ratio_5d" in df.columns:
        base += [
            pl.col("vol_ratio_5d").mean().alias("avg_vol_ratio"),
            (pl.col("vol_ratio_5d") >= 1.5).sum().alias("high_vol_count"),
        ]
    if has_ma20:
        base += [
            pl.when(pl.col("ma20").is_not_null() & (pl.col("ma20") > 0)
                    & (pl.col("close") > pl.col("ma20")))
              .then(1).otherwise(None).sum().alias("_above_cnt"),
            pl.when(pl.col("ma20").is_not_null() & (pl.col("ma20") > 0))
              .then(1).otherwise(None).sum().alias("_valid_cnt"),
        ]
    if has_consec:
        base += ladder_daily_aggs()
    if has_consec and "_prev_consec" in df.columns:
        base += ladder_promo_aggs()

    grouped = df.group_by("date").agg(base).sort("date")

    rows = []
    for r in grouped.iter_rows(named=True):
        up = r.get("up_count", 0) or 0
        down = r.get("down_count", 0) or 0
        total = r.get("total_count", 0) or 0
        limit_up = r.get("limit_up", 0) or 0
        broken = r.get("broken_limit", 0) or 0
        valid_cnt = r.get("_valid_cnt") or 0
        above_cnt = r.get("_above_cnt") or 0
        ma20_above = (above_cnt / valid_cnt) if valid_cnt > 0 else 0.0
        up_pct = (up / total * 100) if total > 0 else 0.0
        down_pct = (down / total * 100) if total > 0 else 0.0
        strong_up_pct = ((r.get("strong_up_count", 0) or 0) / total * 100) if total > 0 else 0.0
        strong_down_pct = ((r.get("strong_down_count", 0) or 0) / total * 100) if total > 0 else 0.0
        avg_pct = r.get("avg_pct", 0.0) or 0.0
        median_pct = r.get("median_pct", 0.0) or 0.0

        metrics = {
            "limit_up": limit_up,
            "limit_down": r.get("limit_down", 0) or 0,
            "broken_limit": broken,
            "max_consecutive": r.get("max_consecutive", 0) or 0,
            "seal_rate": (limit_up / (limit_up + broken))
                         if (limit_up + broken) > 0 else 0.5,
            "up_count": up,
            "down_count": down,
            "up_ratio": (up / down) if down > 0 else (float(up) if up > 0 else 1.0),
            "index_pct": index_pct_map.get(r["date"], 0.0),
            "above_ma20_pct": ma20_above,
            "total_amount": r.get("total_amount", 0) or 0,
            "avg_turnover": r.get("avg_amount", 0) or 0,
            "up_pct": up_pct,
            "down_pct": down_pct,
            "avg_pct": avg_pct,
            "median_pct": median_pct,
            "strong_up_pct": strong_up_pct,
            "strong_down_pct": strong_down_pct,
            "strong_diff_pct": strong_up_pct - strong_down_pct,
            # P1 新增维度所需字段
            "vol_ratio": r.get("avg_vol_ratio") or 0.0,
            "high_vol_ratio": ((r.get("high_vol_count", 0) or 0) / total * 100) if total > 0 else 0.0,
            "consec_ge2": r.get("ge2_count", 0) or 0,
        }
        state, score = classify_state(metrics)
        sub = _compute_subscores(metrics)
        rows.append({
            "date": r["date"],
            "state": state,
            "score": score,
            "limit_up": limit_up,
            "limit_down": metrics["limit_down"],
            "broken_limit": broken,
            "max_consecutive": metrics["max_consecutive"],
            "seal_rate": round(metrics["seal_rate"], 4),
            "up_count": up,
            "down_count": down,
            "up_ratio": round(metrics["up_ratio"], 4),
            "index_pct": round(metrics["index_pct"], 4),
            "above_ma20_pct": round(ma20_above, 4),
            "total_amount": metrics["total_amount"],
            "avg_turnover": metrics["avg_turnover"],
            "avg_pct": round(avg_pct, 4),
            "median_pct": round(median_pct, 4),
            "strong_up_pct": round(strong_up_pct, 4),
            "strong_down_pct": round(strong_down_pct, 4),
            "profit_score": round(sub["profit"]),
            "speculation_score": round(sub["speculation"]),
            "resilience_score": round(sub["resilience"]),
            "index_score": round(sub["index"]),
            "money_score": round(sub["money"]),
            "mainline_score": round(sub["mainline"]),
            **finalize_ladder_row(r),
        })
    return pl.DataFrame(rows) if rows else pl.DataFrame()


# ---------------------------------------------------------------- 时序持久化

def history_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "regime_history.parquet"


def load_history(data_dir: str | Path) -> pl.DataFrame:
    p = history_path(data_dir)
    if not p.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("regime 历史读取失败：%s", e)
        return pl.DataFrame()


def replace_range(data_dir: str | Path, new_rows: pl.DataFrame) -> pl.DataFrame:
    """按 date 覆盖：重算的天覆盖旧行、新天追加，随后整体重标 phase。

    阶段判定（冰点/启动/主升/高潮/退潮/修复）依赖完整日序（EMA 平滑 + 持续性确认），
    不能在单批内完成，所以每次写入后整体重标一次；行数是天数（千级），开销可忽略。
    """
    from .phase import classify_phase_series

    p = history_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    if new_rows is None or new_rows.is_empty():
        return load_history(data_dir)

    old = load_history(data_dir)
    new_dates = set(new_rows["date"].to_list())
    if old.is_empty():
        combined = new_rows
    else:
        kept = old.filter(~pl.col("date").is_in(list(new_dates)))
        # 以新行的列+顺序为权威、旧数据补缺失列，避免 concat 时 schema 不一致
        kept = kept.select([
            pl.col(c) if c in kept.columns else pl.lit(None).alias(c)
            for c in new_rows.columns])
        combined = pl.concat([kept, new_rows.select(new_rows.columns)],
                             how="vertical_relaxed")
    combined = combined.sort("date").unique(subset=["date"], keep="last")

    required = {"max_consecutive", "first_board", "ge2_count", "promo_rate", "seal_rate"}
    if required.issubset(combined.columns):
        try:
            combined = classify_phase_series(combined)
        except Exception as e:  # noqa: BLE001
            logger.warning("phase 标注失败：%s", e)

    combined.write_parquet(p)
    return combined


def coverage(data_dir: str | Path) -> dict:
    df = load_history(data_dir)
    if df.is_empty():
        return {"rows": 0, "earliest_date": None, "latest_date": None}
    return {"rows": df.height,
            "earliest_date": str(df["date"].min()),
            "latest_date": str(df["date"].max())}


def latest_transition(data_dir: str | Path) -> tuple[str, str, str] | None:
    """最近一次阶段切换 → (切换日, 原阶段, 新阶段)；没有历史或没切过则 None。"""
    df = load_history(data_dir)
    if df.is_empty() or "phase" not in df.columns:
        return None
    df = df.sort("date")
    phases = df["phase"].to_list()
    dates = df["date"].to_list()
    for i in range(len(phases) - 1, 0, -1):
        if phases[i] != phases[i - 1]:
            return (str(dates[i]), str(phases[i - 1]), str(phases[i]))
    return None


def earliest_date(data_dir: str | Path) -> date | None:
    df = load_history(data_dir)
    return df["date"].min() if not df.is_empty() else None
