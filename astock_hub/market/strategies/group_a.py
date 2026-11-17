"""策略组 A（8 个）。

入场布尔条件逐字对照 tickflow 同名文件的 matrix compute_signals；
META（params/scoring/limit）照 META；EXIT_SIGNALS/STOP_LOSS/MAX_HOLD_DAYS
→ exit_signal_ids/stop_loss/max_hold_days。
"""

from __future__ import annotations

import polars as pl

from .base import StrategyDef


# ───────── 布林突破 ─────────

def _boll_breakout_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_boll_breakout", True):
        cond = pl.col("close") > pl.col("boll_upper")
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.5)))
    return cond


boll_breakout = StrategyDef(
    id="boll_breakout", name="布林突破",
    description="突破布林上轨 + 放量，强势加速信号",
    tags=["布林", "突破"],
    entry=_boll_breakout_entry,
    scoring={"vol_ratio_5d": 0.4, "change_pct": 0.3, "momentum_20d": 0.3},
    params=[
        {"id": "require_boll_breakout", "label": "要求突破布林上轨", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.5,
         "min": 0.5, "max": 5.0, "step": 0.1},
    ],
    warmup=60, limit=100,
    stop_loss=-0.06,
    max_hold_days=15,
    exit_signal_ids=["signal_boll_breakdown_lower"],
)


# ───────── 放量创60日新高 ─────────

def _breakout_high_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    prior_high = pl.col("high_60d").shift(1).over("symbol")  # 昨日及以前的60日高点
    cond = pl.col("close") > prior_high
    cond = cond & (pl.col("change_pct") >= float(p.get("min_change_pct", 2.0)) / 100.0)
    if p.get("require_volume", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.3)))
    return cond


breakout_new_high_60d = StrategyDef(
    id="breakout_new_high_60d", name="放量创60日新高",
    description="收盘突破前 60 日最高价，量比配合，趋势强度确认",
    tags=["突破", "新高"],
    entry=_breakout_high_entry,
    scoring={"momentum_20d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3},
    params=[
        {"id": "require_volume", "label": "要求量比配合", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.3,
         "min": 0.5, "max": 5.0, "step": 0.1},
        {"id": "min_change_pct", "label": "最低当日涨幅%", "type": "float", "default": 2.0,
         "min": 0.0, "max": 10.0, "step": 0.5},
    ],
    warmup=70, limit=100,
    stop_loss=-0.07,
    max_hold_days=20,
    exit_signal_ids=["signal_break_ma20_lose"],
)


# ───────── 断板反包 ─────────

def _broken_board_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_limit_up", True):
        cond = pl.col("signal_limit_up")
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.5)))
    if p.get("use_change_filter", True):
        cond = cond & (pl.col("change_pct") > float(p.get("change_pct_min", 0.03)))
    return cond


broken_board_recovery = StrategyDef(
    id="broken_board_recovery", name="断板反包",
    description="连板 ≥2 后断板 1-2 天，当日涨停放量反包",
    tags=["涨停", "反包"],
    entry=_broken_board_entry,
    scoring={"change_pct": 0.4, "vol_ratio_5d": 0.3, "momentum_5d": 0.3},
    params=[
        {"id": "require_limit_up", "label": "要求当日涨停", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.5,
         "min": 0.5, "max": 5.0, "step": 0.1},
        {"id": "use_change_filter", "label": "启用涨幅过滤", "type": "bool", "default": True},
        {"id": "change_pct_min", "label": "最低涨幅", "type": "float", "default": 0.03,
         "min": 0.01, "max": 0.10, "step": 0.01},
    ],
    warmup=60, limit=100,
    stop_loss=-0.06,
    max_hold_days=10,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ───────── 均线多头 ─────────

def _bullish_alignment_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_ma_alignment", True):
        cond = ((pl.col("ma5") > pl.col("ma10"))
                & (pl.col("ma10") > pl.col("ma20"))
                & (pl.col("ma20") > pl.col("ma60")))
    if p.get("require_positive_momentum", True):
        cond = cond & (pl.col("momentum_20d") > 0)
    return cond


bullish_alignment = StrategyDef(
    id="bullish_alignment", name="均线多头",
    description="MA5>MA10>MA20>MA60 多头排列 + 短期动量为正",
    tags=["均线", "多头"],
    entry=_bullish_alignment_entry,
    scoring={"momentum_60d": 0.4, "momentum_20d": 0.3, "turnover_rate": 0.3},
    params=[
        {"id": "require_ma_alignment", "label": "要求均线多头排列", "type": "bool", "default": True},
        {"id": "require_positive_momentum", "label": "要求20日动量为正", "type": "bool", "default": True},
    ],
    warmup=60, limit=100,
    stop_loss=-0.06,
    max_hold_days=20,
    exit_signal_ids=["signal_ma_dead_5_20", "signal_ma20_breakdown"],
)


# ───────── 连板股 ─────────

def _consecutive_limit_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_limit_up", True):
        cond = pl.col("signal_limit_up")
    if p.get("use_boards_filter", True):
        cond = cond & (pl.col("consecutive_limit_ups") >= int(p.get("min_boards", 2)))
    return cond


consecutive_limit_ups = StrategyDef(
    id="consecutive_limit_ups", name="连板股",
    description="当日涨停且连续涨停 ≥2 天，强势追涨",
    tags=["涨停", "连板"],
    entry=_consecutive_limit_entry,
    scoring={"consecutive_limit_ups": 0.5, "change_pct": 0.3, "amount": 0.2},
    params=[
        {"id": "require_limit_up", "label": "要求当日涨停", "type": "bool", "default": True},
        {"id": "use_boards_filter", "label": "启用连板数过滤", "type": "bool", "default": True},
        {"id": "min_boards", "label": "最少连板数", "type": "int", "default": 2,
         "min": 1, "max": 20, "step": 1},
    ],
    warmup=60, limit=100,
    stop_loss=-0.05,
    max_hold_days=5,
    exit_signal_ids=[],
)


# ───────── 高换手拉升 ─────────

def _high_turnover_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("use_turnover_filter", True):
        cond = pl.col("turnover_rate") > float(p.get("min_turnover", 5.0))
    if p.get("use_change_filter", True):
        cond = cond & (pl.col("change_pct") > float(p.get("min_change", 3.0)) / 100.0)
    return cond


high_turnover_surge = StrategyDef(
    id="high_turnover_surge", name="高换手拉升",
    description="换手率 > 5% 且涨幅 > 3%，资金活跃",
    tags=["换手率", "放量", "资金"],
    entry=_high_turnover_entry,
    scoring={"turnover_rate": 0.4, "change_pct": 0.3, "momentum_5d": 0.3},
    params=[
        {"id": "use_turnover_filter", "label": "启用换手率过滤", "type": "bool", "default": True},
        {"id": "min_turnover", "label": "最低换手率%", "type": "float", "default": 5.0,
         "min": 1.0, "max": 20.0, "step": 0.5},
        {"id": "use_change_filter", "label": "启用涨幅过滤", "type": "bool", "default": True},
        {"id": "min_change", "label": "最低涨幅%", "type": "float", "default": 3.0,
         "min": 1.0, "max": 10.0, "step": 0.5},
    ],
    warmup=60, limit=50,
    stop_loss=-0.05,
    max_hold_days=10,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ───────── 连板接力 ─────────

def _limit_momentum_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("use_change_filter", True):
        cond = pl.col("change_pct") > float(p.get("min_change", 5.0)) / 100.0
    if p.get("use_boards_filter", True):
        cond = cond & (pl.col("consecutive_limit_ups") >= int(p.get("min_boards", 1)))
    return cond


limit_up_momentum = StrategyDef(
    id="limit_up_momentum", name="连板接力",
    description="连板股 + 今日涨幅 > 5%，连板接力追踪",
    tags=["涨停", "连板", "接力"],
    entry=_limit_momentum_entry,
    scoring={"consecutive_limit_ups": 0.4, "change_pct": 0.3, "amount": 0.3},
    params=[
        {"id": "use_change_filter", "label": "启用涨幅过滤", "type": "bool", "default": True},
        {"id": "min_change", "label": "最低涨幅%", "type": "float", "default": 5.0,
         "min": 2.0, "max": 15.0, "step": 0.5},
        {"id": "use_boards_filter", "label": "启用连板数过滤", "type": "bool", "default": True},
        {"id": "min_boards", "label": "最少连板", "type": "int", "default": 1,
         "min": 1, "max": 10, "step": 1},
    ],
    warmup=60, limit=50,
    stop_loss=-0.05,
    max_hold_days=5,
    exit_signal_ids=[],
)


# ───────── 长下影反击 ─────────

def _long_lower_shadow_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    body_bot = pl.min_horizontal("open", "close")
    shadow_pct = (body_bot - pl.col("low")) / pl.col("prev_close") * 100.0
    rng = pl.col("high") - pl.col("low")
    close_position = pl.when(rng > 0).then(
        (pl.col("close") - pl.col("low")) / rng).otherwise(None)

    cond = shadow_pct >= float(p.get("shadow_pct_min", 3.0))
    cond = cond & (pl.col("momentum_5d") <= float(p.get("drop_pct_max", -5.0)) / 100.0)
    cond = cond & (pl.col("vol_ratio_5d") >= 1.2)
    if p.get("require_recovery", True):
        cond = cond & (close_position >= 0.5)
    return cond


long_lower_shadow_reversal = StrategyDef(
    id="long_lower_shadow_reversal", name="长下影反击",
    description="近 5 日超跌后收长下影线、收盘收复当日振幅上半部，下方承接强的反转信号",
    tags=["K线形态", "超跌", "反转"],
    entry=_long_lower_shadow_entry,
    scoring={"momentum_20d": 0.3, "vol_ratio_5d": 0.4, "change_pct": 0.3},
    params=[
        {"id": "shadow_pct_min", "label": "下影线最低长度%（相对昨收）", "type": "float",
         "default": 3.0, "min": 1.0, "max": 10.0, "step": 0.5},
        {"id": "drop_pct_max", "label": "近5日累计跌幅下限%（负值）", "type": "float",
         "default": -5.0, "min": -30.0, "max": 0.0, "step": 1.0},
        {"id": "require_recovery", "label": "要求收盘收复上半区", "type": "bool", "default": True},
    ],
    warmup=20, limit=100,
    stop_loss=-0.05,
    max_hold_days=10,
    exit_signal_ids=["signal_ma5_lose_after_shadow"],
)


STRATEGIES = [
    boll_breakout, breakout_new_high_60d, broken_board_recovery,
    bullish_alignment, consecutive_limit_ups, high_turnover_surge,
    limit_up_momentum, long_lower_shadow_reversal,
]
