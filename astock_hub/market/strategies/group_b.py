"""策略组 B（8 个）。

入场布尔条件逐字对照 tickflow 同名文件的 matrix compute_signals；
META（params/scoring/limit）照 META；EXIT_SIGNALS/STOP_LOSS/MAX_HOLD_DAYS
→ exit_signal_ids/stop_loss/max_hold_days。

near_limit_up 用到的 price_limit_pct（当日涨停幅度）帧里没有，按板块规则内联推导
（创业板/科创板 20%、北交所 30%、其余 10%），与 price_limits 的板块口径一致。
"""

from __future__ import annotations

import polars as pl

from .base import StrategyDef


# ───── 低波动龙头 ─────

def _low_volatility_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_positive_momentum", True):
        cond = pl.col("momentum_20d") > 0
    if p.get("use_volatility_filter", True):
        cond = cond & (pl.col("annual_vol_20d") < float(p.get("vol_max", 0.30)))
    if p.get("require_above_ma20", True):
        cond = cond & (pl.col("close") > pl.col("ma20"))
    return cond


low_volatility_leader = StrategyDef(
    id="low_volatility_leader", name="低波动龙头",
    description="20 日动量为正 + 年化波动 < 30% + 收盘价在 MA20 上方",
    tags=["低波动", "龙头"],
    entry=_low_volatility_entry,
    scoring={"momentum_60d": 0.4, "momentum_20d": 0.3, "turnover_rate": 0.3},
    params=[
        {"id": "require_positive_momentum", "label": "要求20日动量为正", "type": "bool", "default": True},
        {"id": "use_volatility_filter", "label": "启用波动率过滤", "type": "bool", "default": True},
        {"id": "vol_max", "label": "最大年化波动", "type": "float", "default": 0.30,
         "min": 0.05, "max": 1.0, "step": 0.01},
        {"id": "require_above_ma20", "label": "要求收盘价在MA20上方", "type": "bool", "default": True},
    ],
    warmup=60, limit=100,
    stop_loss=-0.05,
    max_hold_days=30,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ───── 均线粘合突破 ─────

def _ma_convergence_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    band_top = pl.max_horizontal("ma5", "ma10", "ma20")
    band_bot = pl.min_horizontal("ma5", "ma10", "ma20")
    spread = (band_top - band_bot).abs() / pl.col("ma20") * 100.0
    tight = spread <= float(p.get("spread_pct_max", 2.5))
    days = max(2, int(p.get("squeeze_days", 5)))
    # 前 days 天（含昨日、不含今日）带宽持续低于阈值：tight 的 shift(1) 窗口求和 == days
    squeeze = tight.cast(pl.Int64).shift(1).rolling_sum(days).over("symbol") == days

    cond = squeeze & (pl.col("close") > band_top)
    cond = cond & (pl.col("change_pct") >= float(p.get("min_change_pct", 2.0)) / 100.0)
    if p.get("require_volume", True):
        cond = cond & (pl.col("vol_ratio_5d") >= 1.2)
    return cond


ma_convergence_breakout = StrategyDef(
    id="ma_convergence_breakout", name="均线粘合突破",
    description="MA5/10/20 粘合收敛后放量向上突破，挤压释放的变盘启动点",
    tags=["均线", "粘合", "突破"],
    entry=_ma_convergence_entry,
    scoring={"vol_ratio_5d": 0.4, "change_pct": 0.3, "momentum_20d": 0.3},
    params=[
        {"id": "spread_pct_max", "label": "粘合带宽上限%（三线极差/MA20）", "type": "float",
         "default": 2.5, "min": 0.5, "max": 8.0, "step": 0.5},
        {"id": "squeeze_days", "label": "粘合持续天数", "type": "int", "default": 5,
         "min": 3, "max": 15, "step": 1},
        {"id": "min_change_pct", "label": "突破日最低涨幅%", "type": "float", "default": 2.0,
         "min": 0.0, "max": 10.0, "step": 0.5},
        {"id": "require_volume", "label": "要求量比配合", "type": "bool", "default": True},
    ],
    warmup=25, limit=100,
    stop_loss=-0.06,
    max_hold_days=15,
    exit_signal_ids=["signal_ma10_lose"],
)


# ───── MACD 零下回升 ─────

def _macd_revival_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    win = max(5, int(p.get("low_window", 20)))
    revive = max(3, int(p.get("revive_days", 10)))
    # 阶段新低：收盘 <= 前 win 日（不含今日）最低收盘
    prior_min = pl.col("close").shift(1).rolling_min(win).over("symbol")
    new_low = pl.col("close") <= prior_min
    return (new_low & (pl.col("macd_dif") < 0)
            & (pl.col("macd_dif") > pl.col("macd_dif").shift(revive).over("symbol")))


macd_below_zero_revival = StrategyDef(
    id="macd_below_zero_revival", name="MACD 零下回升",
    description="股价创阶段新低而 MACD DIF 拒绝新低并回升（底背离简化），零轴下方动能修复",
    tags=["MACD", "背离", "超跌"],
    entry=_macd_revival_entry,
    scoring={"momentum_20d": 0.4, "change_pct": 0.3, "vol_ratio_5d": 0.3},
    params=[
        {"id": "low_window", "label": "新低回看天数", "type": "int", "default": 20,
         "min": 10, "max": 60, "step": 5},
        {"id": "revive_days", "label": "DIF 回升对比天数", "type": "int", "default": 10,
         "min": 5, "max": 20, "step": 1},
    ],
    warmup=60, limit=100,
    stop_loss=-0.06,
    max_hold_days=20,
    exit_signal_ids=["signal_macd_golden_above_zero"],
)


# ───── MACD 金叉放量 ─────

def _macd_golden_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_macd_golden", True):
        cond = pl.col("signal_macd_golden")
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.5)))
    return cond


macd_golden = StrategyDef(
    id="macd_golden", name="MACD 金叉放量",
    description="MACD 金叉当日 + 量能放大",
    tags=["MACD", "金叉", "放量"],
    entry=_macd_golden_entry,
    scoring={"momentum_60d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3},
    params=[
        {"id": "require_macd_golden", "label": "要求MACD金叉", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.5,
         "min": 0.5, "max": 5.0, "step": 0.1},
    ],
    warmup=60, limit=100,
    stop_loss=-0.07,
    max_hold_days=20,
    exit_signal_ids=["signal_macd_dead"],
)


# ───── 新低反转 ─────

def _n_day_low_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("require_n_day_low", True):
        cond = pl.col("close") <= pl.col("low_60d")
    if p.get("require_bullish_candle", True):
        cond = cond & (pl.col("close") > pl.col("open"))
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.5)))
    return cond


n_day_low_reversal = StrategyDef(
    id="n_day_low_reversal", name="新低反转",
    description="触及 60 日新低后当日收阳放量，反转信号",
    tags=["反转", "新低"],
    entry=_n_day_low_entry,
    scoring={"change_pct": 0.4, "vol_ratio_5d": 0.3, "momentum_5d": 0.3},
    params=[
        {"id": "require_n_day_low", "label": "要求60日新低", "type": "bool", "default": True},
        {"id": "require_bullish_candle", "label": "要求收阳", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.5,
         "min": 0.5, "max": 5.0, "step": 0.1},
    ],
    warmup=60, limit=100,
    stop_loss=-0.06,
    max_hold_days=15,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ───── 逼近涨停 ─────

def _upper_limit_pct_expr() -> pl.Expr:
    """当日涨停幅度：创业板（300/301）、科创板（688/689）20%，北交所（.BJ）30%，其余 10%。"""
    s = pl.col("symbol")
    is_growth_star = s.str.starts_with("300") | s.str.starts_with("301") \
        | s.str.starts_with("688") | s.str.starts_with("689")
    is_bj = s.str.ends_with(".BJ")
    return pl.when(is_growth_star).then(0.20).when(is_bj).then(0.30).otherwise(0.10)


def _near_limit_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("use_change_filter", True):
        cond = pl.col("change_pct") > float(p.get("min_change", 7.0)) / 100.0
    if p.get("use_limit_gap_filter", True):
        limit_pct = _upper_limit_pct_expr()
        cond = cond & (pl.col("change_pct")
                       >= limit_pct - float(p.get("limit_gap", 3.0)) / 100.0)
    return cond


near_limit_up = StrategyDef(
    id="near_limit_up", name="逼近涨停",
    description="涨幅 > 7% 且距涨停空间 < 3%，追涨信号",
    tags=["涨停", "追涨"],
    entry=_near_limit_entry,
    scoring={"change_pct": 0.5, "amount": 0.3, "momentum_5d": 0.2},
    params=[
        {"id": "use_change_filter", "label": "启用涨幅过滤", "type": "bool", "default": True},
        {"id": "min_change", "label": "最低涨幅%", "type": "float", "default": 7.0,
         "min": 3.0, "max": 15.0, "step": 1.0},
        {"id": "use_limit_gap_filter", "label": "启用距涨停空间过滤", "type": "bool", "default": True},
        {"id": "limit_gap", "label": "距涨停空间%", "type": "float", "default": 3.0,
         "min": 1.0, "max": 10.0, "step": 0.5},
    ],
    warmup=60, limit=50,
    stop_loss=-0.05,
    max_hold_days=5,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ───── 超跌反弹 ─────

def _oversold_bounce_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    cond = pl.lit(True)
    if p.get("use_rsi_filter", True):
        cond = pl.col("rsi_14") < float(p.get("rsi_max", 30.0))
    if p.get("require_bullish_candle", True):
        cond = cond & (pl.col("close") > pl.col("open"))
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.2)))
    return cond


oversold_bounce = StrategyDef(
    id="oversold_bounce", name="超跌反弹",
    description="RSI14 < 30 超卖区 + 当日收阳 + 放量，抄底信号",
    tags=["超跌", "反弹", "RSI"],
    entry=_oversold_bounce_entry,
    scoring={"change_pct": 0.3, "vol_ratio_5d": 0.3, "momentum_5d": 0.2, "rsi_14": 0.2},
    params=[
        {"id": "use_rsi_filter", "label": "启用RSI过滤", "type": "bool", "default": True},
        {"id": "rsi_max", "label": "RSI上限", "type": "float", "default": 30.0,
         "min": 10.0, "max": 50.0, "step": 1.0},
        {"id": "require_bullish_candle", "label": "要求收阳", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.2,
         "min": 0.5, "max": 5.0, "step": 0.1},
    ],
    warmup=60, limit=100,
    stop_loss=-0.05,
    max_hold_days=15,
    exit_signal_ids=["signal_ma20_breakdown"],
)


# ───── 超跌反转 ─────

def _oversold_reversal_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # entry = RSI14 超卖 & 当日涨幅达标 & 收盘站上 MA5
    cond = pl.lit(True)
    if p.get("use_rsi_filter", True):
        cond = cond & (pl.col("rsi_14") < float(p.get("rsi_max", 30.0)))
    if p.get("use_change_filter", True):
        cond = cond & (pl.col("change_pct") > float(p.get("min_change", 1.0)) / 100.0)
    if p.get("require_above_ma5", True):
        cond = cond & (pl.col("close") > pl.col("ma5"))
    return cond


oversold_reversal = StrategyDef(
    id="oversold_reversal",
    name="超跌反转",
    description="RSI14 < 30超卖 + 涨幅 > 1% + 站上MA5, 超卖反转信号",
    tags=["超跌", "反弹", "RSI"],
    entry=_oversold_reversal_entry,
    scoring={"change_pct": 0.4, "rsi_14": 0.3, "vol_ratio_5d": 0.3},
    params=[
        {"id": "use_rsi_filter", "label": "启用RSI过滤", "type": "bool", "default": True},
        {"id": "rsi_max", "label": "RSI上限", "type": "float", "default": 30.0,
         "min": 10.0, "max": 50.0, "step": 1.0},
        {"id": "use_change_filter", "label": "启用涨幅过滤", "type": "bool", "default": True},
        {"id": "min_change", "label": "最低涨幅%", "type": "float", "default": 1.0,
         "min": 0.5, "max": 5.0, "step": 0.5},
        {"id": "require_above_ma5", "label": "要求收盘价在MA5上方", "type": "bool",
         "default": True},
    ],
    warmup=60,
    limit=50,
    stop_loss=-0.05,
    max_hold_days=15,
    exit_signal_ids=["signal_ma20_breakdown"],
)


STRATEGIES = [
    low_volatility_leader, ma_convergence_breakout,
    macd_below_zero_revival, macd_golden, n_day_low_reversal,
    near_limit_up, oversold_bounce, oversold_reversal,
]
