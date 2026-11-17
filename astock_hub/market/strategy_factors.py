"""策略专用的辅助因子：入场/打分引用、但基础指标 pipeline 没直接产出的滚动列。

在全市场 enriched 帧上一次性物化（列名与 tickflow 的虚拟因子一致）：
- limit_up_count_20d / limit_up_count_60d：窗口内涨停天数（rolling sum，min_samples=窗口，
  与 tickflow scoring_value_expr 口径一致——历史不足给 null，不填 0）。
- up_days_20d：20 日里上涨的天数。
- turnover_ratio_5d：当日换手率 / 前 5 日平均换手率。**需要 turnover_rate 列**，
  astock-hub 全市场帧默认没有历史换手率（缺流通股本），缺列时不物化，打分端自动跳过该因子。

注意"涨停"的判定用 signal_limit_up（pipeline 已按原始价+涨跌停规则算好），
不要用 change_pct>=0.09 近似（创业板 20%、且精度/一字板会误判）。
"""

from __future__ import annotations

import polars as pl


def add_strategy_factors(frame: pl.DataFrame) -> pl.DataFrame:
    """给全市场日K帧补上策略可能引用的滚动因子列（已存在的不重算）。"""
    hit = pl.col("signal_limit_up").cast(pl.Int64).fill_null(0)
    up = pl.col("change_pct").gt(0).cast(pl.Int64)

    specs = [
        ("limit_up_count_20d", hit, 20),
        ("limit_up_count_60d", hit, 60),
        ("up_days_20d", up, 20),
    ]
    exprs = []
    for name, base_expr, window in specs:
        if name in frame.columns:
            continue
        exprs.append(
            base_expr.rolling_sum(window, min_samples=window)
            .over("symbol").alias(name))
    if exprs:
        frame = frame.with_columns(exprs)

    # 换手率比：只有帧里带 turnover_rate 才算（前 5 日均不含当天，标准量比口径）
    if "turnover_rate" in frame.columns and "turnover_ratio_5d" not in frame.columns:
        prev_avg = (pl.col("turnover_rate").shift(1)
                    .rolling_mean(5).over("symbol"))
        frame = frame.with_columns(
            pl.when(prev_avg > 0)
              .then(pl.col("turnover_rate") / prev_avg)
              .otherwise(None).alias("turnover_ratio_5d"))
    return frame
