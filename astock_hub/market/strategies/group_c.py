"""策略组 C（7 个）。

入场布尔条件逐字对照 tickflow 同名文件的 matrix compute_signals；
META（params/scoring/limit）照 META；EXIT_SIGNALS/STOP_LOSS/MAX_HOLD_DAYS
→ exit_signal_ids/stop_loss/max_hold_days。
"""

from __future__ import annotations

import polars as pl

from .base import StrategyDef


# ─────────────────────── 平台整理突破 ───────────────────────

def _platform_consolidation_breakout_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    days = max(4, int(p.get("platform_days", 10)))
    # 平台区间: 前 days 日 (不含今日) 的最高/最低，滚动窗口平移 1 根
    prior_high = (
        pl.col("high").shift(1).rolling_max(window_size=days).over("symbol")
    )
    prior_low = (
        pl.col("low").shift(1).rolling_min(window_size=days).over("symbol")
    )
    # 与 matrix range_pct = (prior_high - prior_low) / close * 100 一致
    range_pct = (prior_high - prior_low) / pl.col("close") * 100.0
    return (
        (range_pct <= float(p.get("range_pct_max", 8.0)))
        & (pl.col("close") > prior_high)
        & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.5)))
    )


platform_consolidation_breakout = StrategyDef(
    id="platform_consolidation_breakout",
    name="平台整理突破",
    description="近N日窄幅横盘 (振幅收敛) 后放量突破平台上沿, 蓄势变盘",
    tags=["形态", "平台", "突破"],
    entry=_platform_consolidation_breakout_entry,
    scoring={"vol_ratio_5d": 0.4, "momentum_20d": 0.3, "change_pct": 0.3},
    params=[
        {"id": "platform_days", "label": "平台天数", "type": "int", "default": 10,
         "min": 5, "max": 30, "step": 1},
        {"id": "range_pct_max", "label": "平台振幅上限%", "type": "float",
         "default": 8.0, "min": 3.0, "max": 20.0, "step": 0.5},
        {"id": "vol_ratio_min", "label": "突破日最低量比", "type": "float",
         "default": 1.5, "min": 1.0, "max": 5.0, "step": 0.1},
    ],
    warmup=20,
    limit=100,
    stop_loss=-0.06,
    max_hold_days=15,
    exit_signal_ids=["signal_platform_fail_ma20"],
)


# ─────────────────────── 均线回踩反弹 ───────────────────────

def _pullback_ma20_bounce_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # 注意: ENTRY_SIGNALS 虽标金叉，但实际入场是"回踩 MA20"，以 compute_signals 为准
    cond = pl.lit(True)
    if p.get("use_ma20_proximity", True):
        proximity = float(p.get("ma_proximity", 2.0)) / 100.0
        cond = cond & (
            (pl.col("close") > pl.col("ma20") * (1.0 - proximity))
            & (pl.col("close") < pl.col("ma20") * (1.0 + proximity))
        )
    if p.get("require_ma_alignment", True):
        cond = cond & (pl.col("ma5") > pl.col("ma20")) & (pl.col("ma20") > pl.col("ma60"))
    if p.get("require_positive_change", True):
        cond = cond & (pl.col("change_pct") > 0)
    return cond


pullback_ma20_bounce = StrategyDef(
    id="pullback_ma20_bounce",
    name="均线回踩反弹",
    description="价格在MA20附近(±2%)且MA5>MA20>MA60多头排列, 回踩买入",
    tags=["回踩", "均线", "反弹"],
    entry=_pullback_ma20_bounce_entry,
    scoring={"momentum_60d": 0.4, "change_pct": 0.3, "momentum_20d": 0.3},
    params=[
        {"id": "use_ma20_proximity", "label": "启用MA20附近过滤", "type": "bool",
         "default": True},
        {"id": "ma_proximity", "label": "MA偏离度%", "type": "float", "default": 2.0,
         "min": 0.5, "max": 5.0, "step": 0.5},
        {"id": "require_ma_alignment", "label": "要求MA5>MA20>MA60", "type": "bool",
         "default": True},
        {"id": "require_positive_change", "label": "要求当日上涨", "type": "bool",
         "default": True},
    ],
    warmup=60,
    limit=50,
    stop_loss=-0.05,
    max_hold_days=15,
    exit_signal_ids=["signal_ma20_breakdown", "signal_ma_dead_5_20"],
)


# ──────────────────────── 缩量回踩 ─────────────────────────

def _pullback_to_support_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # 注意: 与上一策略不同，此处 ma_proximity 默认值是小数(0.02)，matrix 直接当比例用
    cond = pl.lit(True)
    if p.get("use_ma20_proximity", True):
        proximity = float(p.get("ma_proximity", 0.02))
        cond = cond & (
            (pl.col("close") > pl.col("ma20") * (1.0 - proximity))
            & (pl.col("close") < pl.col("ma20") * (1.0 + proximity))
        )
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") < float(p.get("vol_ratio_max", 0.8)))
    if p.get("require_above_ma60", True):
        cond = cond & (pl.col("close") > pl.col("ma60"))
    if p.get("require_positive_momentum", True):
        cond = cond & (pl.col("momentum_20d") > 0)
    return cond


pullback_to_support = StrategyDef(
    id="pullback_to_support",
    name="缩量回踩",
    description="回踩MA20附近 + 缩量 + 中期趋势向上",
    tags=["回踩", "支撑"],
    entry=_pullback_to_support_entry,
    scoring={"momentum_60d": 0.4, "momentum_20d": 0.3, "turnover_rate": 0.3},
    params=[
        {"id": "use_ma20_proximity", "label": "启用MA20附近过滤", "type": "bool",
         "default": True},
        {"id": "ma_proximity", "label": "均线偏离度", "type": "float", "default": 0.02,
         "min": 0.01, "max": 0.05, "step": 0.005},
        {"id": "use_volume_filter", "label": "启用缩量过滤", "type": "bool",
         "default": True},
        {"id": "vol_ratio_max", "label": "最大量比", "type": "float", "default": 0.8,
         "min": 0.2, "max": 1.5, "step": 0.1},
        {"id": "require_above_ma60", "label": "要求收盘价在MA60上方", "type": "bool",
         "default": True},
        {"id": "require_positive_momentum", "label": "要求20日动量为正", "type": "bool",
         "default": True},
    ],
    warmup=60,
    limit=100,
    stop_loss=-0.05,
    max_hold_days=20,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ─────────────────────── RSI 中轴回踩 ──────────────────────

def _rsi_midline_pullback_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    rsi_col = f"rsi_{int(p.get('rsi_period', 14))}"
    lo = float(p.get("mid_low", 45.0))
    hi = float(p.get("mid_high", 60.0))
    # 近5日(shift 1..5)曾出现 RSI > hi+5，即强势记忆
    was_strong = (
        pl.col(rsi_col).shift(1).rolling_max(window_size=5).over("symbol") > hi + 5.0
    )
    return (
        (pl.col("close") > pl.col("ma60"))
        & (pl.col(rsi_col) >= lo)
        & (pl.col(rsi_col) <= hi)
        & was_strong
    )


rsi_midline_pullback = StrategyDef(
    id="rsi_midline_pullback",
    name="RSI 中轴回踩",
    description="多头趋势 (MA60 上方) 中 RSI 回落至 50 中轴区间企稳, 强势股低吸",
    tags=["RSI", "趋势", "低吸"],
    entry=_rsi_midline_pullback_entry,
    scoring={"momentum_20d": 0.4, "up_days_20d": 0.3, "vol_ratio_5d": 0.3},
    params=[
        {"id": "rsi_period", "label": "RSI 周期", "type": "int", "default": 14,
         "min": 6, "max": 24, "step": 2},
        {"id": "mid_low", "label": "中轴区间下沿", "type": "float", "default": 45.0,
         "min": 30.0, "max": 55.0, "step": 1.0},
        {"id": "mid_high", "label": "中轴区间上沿", "type": "float", "default": 60.0,
         "min": 45.0, "max": 70.0, "step": 1.0},
    ],
    warmup=74,
    limit=100,
    stop_loss=-0.06,
    max_hold_days=15,
    exit_signal_ids=["signal_rsi_midline_fail"],
)


# ──────────────────────── 强势高开 ─────────────────────────

def _strong_open_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # 日K无集合竞价实时字段，高开口径用 open vs 昨收(prev_close)，与 matrix 一致
    cond = pl.lit(True)
    if p.get("use_open_gap_filter", True):
        cond = cond & (
            pl.col("open")
            > pl.col("prev_close")
            * (1.0 + float(p.get("min_open_gap", 3.0)) / 100.0)
        )
    if p.get("require_close_above_open", True):
        cond = cond & (pl.col("close") > pl.col("open"))
    if p.get("use_change_filter", True):
        cond = cond & (
            pl.col("change_pct") > float(p.get("min_change", 3.0)) / 100.0
        )
    return cond


strong_open = StrategyDef(
    id="strong_open",
    name="强势高开",
    description="高开 > 3% 且收盘高于开盘价, 集合竞价强势",
    tags=["高开", "强势"],
    entry=_strong_open_entry,
    scoring={"change_pct": 0.4, "amplitude": 0.2, "amount": 0.4},
    params=[
        {"id": "use_open_gap_filter", "label": "启用高开过滤", "type": "bool",
         "default": True},
        {"id": "min_open_gap", "label": "最低高开%", "type": "float", "default": 3.0,
         "min": 1.0, "max": 10.0, "step": 0.5},
        {"id": "require_close_above_open", "label": "要求收盘高于开盘", "type": "bool",
         "default": True},
        {"id": "use_change_filter", "label": "启用涨幅过滤", "type": "bool",
         "default": True},
        {"id": "min_change", "label": "最低涨幅%", "type": "float", "default": 3.0,
         "min": 1.0, "max": 10.0, "step": 0.5},
    ],
    warmup=60,
    limit=50,
    stop_loss=-0.05,
    max_hold_days=10,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ──────────────────────── 趋势突破 ─────────────────────────

def _trend_breakout_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # entry = MA60 上方 & 收盘创 60 日新高 & 量比达标
    cond = pl.lit(True)
    if p.get("require_above_ma60", True):
        cond = cond & (pl.col("close") > pl.col("ma60"))
    if p.get("require_n_day_high", True):
        cond = cond & (pl.col("close") >= pl.col("high_60d"))
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 2.0)))
    return cond


trend_breakout = StrategyDef(
    id="trend_breakout",
    name="趋势突破",
    description="MA60上方 + 60日新高 + 量能 ≥ 2倍均量",
    tags=["趋势", "突破", "放量"],
    entry=_trend_breakout_entry,
    scoring={"momentum_60d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3},
    params=[
        {"id": "require_above_ma60", "label": "要求收盘价在MA60上方", "type": "bool",
         "default": True},
        {"id": "require_n_day_high", "label": "要求60日新高", "type": "bool",
         "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool",
         "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 2.0,
         "min": 0.5, "max": 10.0, "step": 0.1},
    ],
    warmup=60,
    limit=100,
    stop_loss=-0.08,
    max_hold_days=20,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ──────────────────────── 量价齐升 ─────────────────────────

def _volume_price_surge_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # breakout = close>ma20 & shift(close)<=shift(ma20)，pipeline 已物化 signal_ma20_breakout
    cond = pl.lit(True)
    if p.get("require_ma20_breakout", True):
        cond = cond & pl.col("signal_ma20_breakout")
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 2.0)))
    if p.get("require_bullish_candle", True):
        cond = cond & (pl.col("close") > pl.col("open"))
    return cond


volume_price_surge = StrategyDef(
    id="volume_price_surge",
    name="量价齐升",
    description="突破MA20 + 放量 + 收阳",
    tags=["量价", "突破"],
    entry=_volume_price_surge_entry,
    scoring={"vol_ratio_5d": 0.4, "change_pct": 0.3, "momentum_20d": 0.3},
    params=[
        {"id": "require_ma20_breakout", "label": "要求突破MA20", "type": "bool",
         "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool",
         "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 2.0,
         "min": 0.5, "max": 10.0, "step": 0.1},
        {"id": "require_bullish_candle", "label": "要求收阳", "type": "bool",
         "default": True},
    ],
    warmup=60,
    limit=100,
    stop_loss=-0.06,
    max_hold_days=15,
    exit_signal_ids=["signal_ma20_breakdown"],
)


STRATEGIES = [
    platform_consolidation_breakout,
    pullback_ma20_bounce,
    pullback_to_support,
    rsi_midline_pullback,
    strong_open,
    trend_breakout,
    volume_price_surge,
]
