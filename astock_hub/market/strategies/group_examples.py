"""范例策略（2 个）：ma_golden_cross / active_limit_gene。

这两个作为其它策略翻译的**写法模板**。入场逻辑逐字对照 tickflow 对应文件的
compute_signals matrix 布尔表达式；参数与打分取 META。
"""

from __future__ import annotations

import polars as pl

from .base import StrategyDef


# ───────────────────────── MA 金叉 ─────────────────────────

def _ma_golden_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    """MA 金叉策略：MA5 上穿 MA20 当日触发，量能配合，且收盘价在 MA60 上方。

    注：使用 SMA（简单移动平均），与 tickflow 对齐。
    """
    cond = pl.lit(True)
    if p.get("require_ma_golden", True):
        # 使用 MA5 和 MA20 的金叉信号（SMA）
        cond = (
            (pl.col("ma5") > pl.col("ma20")) &
            (pl.col("ma5").shift(1).over("symbol") <= pl.col("ma20").shift(1).over("symbol"))
        )
    if p.get("use_volume_filter", True):
        cond = cond & (pl.col("vol_ratio_5d") >= float(p.get("vol_ratio_min", 1.2)))
    if p.get("require_above_ma60", True):
        cond = cond & (pl.col("close") > pl.col("ma60"))
    return cond


ma_golden_cross = StrategyDef(
    id="ma_golden_cross",
    name="MA 金叉",
    description="MA5 上穿 MA20 当日触发，量能配合，且收盘价在 MA60 上方",
    tags=["均线", "金叉"],
    entry=_ma_golden_entry,
    scoring={"momentum_20d": 0.5, "vol_ratio_5d": 0.3, "change_pct": 0.2},
    params=[
        {"id": "require_ma_golden", "label": "要求 MA5 上穿 MA20", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {"id": "vol_ratio_min", "label": "最低量比", "type": "float", "default": 1.2,
         "min": 0.5, "max": 5.0, "step": 0.1},
        {"id": "require_above_ma60", "label": "要求收盘价在 MA60 上方", "type": "bool", "default": True},
    ],
    warmup=60,
    limit=100,
    stop_loss=-0.06,  # 止损 -6%
    max_hold_days=15,  # 最大持有 15 天
    exit_signal_ids=["signal_ma_dead_5_20"],  # 退出信号：MA5 死叉 MA20
)


# ───────────────────── 涨停基因活跃股 ─────────────────────

def _active_limit_gene_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # entry = 近60日涨停数>=阈值 & 当日涨幅<上限(排除涨停) & 量比<=上限(休整)
    return (
        (pl.col("limit_up_count_60d") >= int(p.get("min_limit_count", 3)))
        & (pl.col("change_pct") < float(p.get("max_change_pct", 7.0)) / 100.0)
        & (pl.col("vol_ratio_5d") <= float(p.get("vol_ratio_max", 1.2)))
    )


active_limit_gene = StrategyDef(
    id="active_limit_gene",
    name="涨停基因活跃股",
    description="近 60 日涨停次数达标，且当日未涨停、缩量休整的活跃标的（股性筛选，非追板）",
    tags=["涨停", "活跃", "股性"],
    entry=_active_limit_gene_entry,
    scoring={"limit_up_count_60d": 0.4, "momentum_20d": 0.3, "turnover_ratio_5d": 0.3},
    params=[
        {"id": "min_limit_count", "label": "近 60 日最少涨停次数", "type": "int",
         "default": 3, "min": 2, "max": 10, "step": 1},
        {"id": "vol_ratio_max", "label": "当日量比上限（休整）", "type": "float",
         "default": 1.2, "min": 0.5, "max": 3.0, "step": 0.1},
        {"id": "max_change_pct", "label": "当日涨幅上限%（排除涨停）", "type": "float",
         "default": 7.0, "min": 3.0, "max": 15.0, "step": 0.5},
    ],
    warmup=70,
    limit=100,
    stop_loss=-0.08,  # 止损 -8%
    max_hold_days=25,  # 最大持有 25 天
    exit_signal_ids=["limit_up_count_60d"],  # 退出信号：涨停基因减弱
)
