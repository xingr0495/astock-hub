"""技术指标与信号计算（从 tickflow `backend/app/indicators/pipeline.py` 移植）。

移植原则：**公式逐字照抄**，只剥离与 astock-hub 无关的部分。这些数字用于投资
决策，口径漂移是最大的风险，因此原文件里若干"反直觉但有意为之"的约定一并保留：

- EMA 家族（ema/macd/rsi/kdj/atr）一律 `adjust=False`，即递推式指数平滑；
- RSI 用 Wilder 平滑（`alpha=1/n`），KDJ 用 `alpha=1/3`；
- `high_60d` / `low_60d` 是 **收盘价** 的 60 日滚动极值（不是 high/low 的极值）；
- `vol_ratio_5d` 的分母是 `volume.shift(1).rolling_mean(5)`——"前 5 日均量**不含当天**"，
  这是同花顺/东财的标准量比口径；
- 涨跌停价用整数分算术（`price_limits.polars_limit_price`），避免浮点误差差一分钱。

相对 tickflow 原版删掉了三处与 astock-hub 无关的东西（数值不受影响）：
1. `needed=` 列裁剪开关——那是给全市场批量算做的性能优化，astock-hub 先算全量；
   原注释也确认"跳过其它列不影响保留列的数值"。
2. 自定义信号 / 扩展因子（依赖 tickflow 的因子注册表），未随本次搬迁。
3. `turnover_rate`（换手率）——它需要流通股本，hithink 本地库没有该列，
   换手率目前由行情源（iFinD/同花顺快照）直接提供。
"""

from __future__ import annotations

import polars as pl

from .price_limits import (
    polars_limit_price,
    polars_price_limit_pct,
)

# 原子信号 → 依赖的指标列（供调用方判断某信号能否算出）
SIGNAL_DEPENDENCIES: dict[str, frozenset[str]] = {
    "signal_ma_golden_5_20": frozenset({"ma5", "ma20"}),
    "signal_ma_dead_5_20": frozenset({"ma5", "ma20"}),
    "signal_ma_golden_20_60": frozenset({"ma20", "ma60"}),
    "signal_macd_golden": frozenset({"macd_dif", "macd_dea"}),
    "signal_macd_dead": frozenset({"macd_dif", "macd_dea"}),
    "signal_ma20_breakout": frozenset({"close", "ma20"}),
    "signal_ma20_breakdown": frozenset({"close", "ma20"}),
    "signal_ma5_breakout": frozenset({"close", "ma5"}),
    "signal_ma5_breakdown": frozenset({"close", "ma5"}),
    "signal_ma10_breakout": frozenset({"close", "ma10"}),
    "signal_ma10_breakdown": frozenset({"close", "ma10"}),
    "signal_n_day_high": frozenset({"close", "high_60d"}),
    "signal_n_day_low": frozenset({"close", "low_60d"}),
    "signal_boll_breakout_upper": frozenset({"close", "boll_upper"}),
    "signal_boll_breakdown_lower": frozenset({"close", "boll_lower"}),
    "signal_volume_surge": frozenset({"vol_ratio_5d"}),
}

_INDICATOR_TEMP_COLS = [
    "_boll_std", "_tr", "_ema12", "_ema26",
    "_kdj_ln", "_kdj_hn", "_vol_ma5_prev", "_daily_pct",
    "_delta", "_gain", "_loss",
    "_rsi_avg_gain_6", "_rsi_avg_loss_6",
    "_rsi_avg_gain_14", "_rsi_avg_loss_14",
    "_rsi_avg_gain_24", "_rsi_avg_loss_24",
]


def _ema_alpha(span: int) -> float:
    """EMA 平滑系数 2/(span+1)。"""
    return 2.0 / (span + 1)


def compute_indicators(df: pl.DataFrame) -> pl.DataFrame:
    """从 OHLCV 计算全套技术指标。

    输入必须包含: symbol, date, open, high, low, close, volume
    """
    if df.is_empty():
        return df

    df = df.sort(["symbol", "date"])
    prev_close = pl.col("close").shift(1).over("symbol")

    # Pass 1: 均线 + EMA + MACD/BOLL/KDJ/ATR 基础 + 量价 + 极值
    df = df.with_columns([
        prev_close.alias("prev_close"),
        pl.col("close").rolling_mean(5).over("symbol").alias("ma5"),
        pl.col("close").rolling_mean(10).over("symbol").alias("ma10"),
        pl.col("close").rolling_mean(20).over("symbol").alias("ma20"),
        pl.col("close").rolling_mean(30).over("symbol").alias("ma30"),
        pl.col("close").rolling_mean(60).over("symbol").alias("ma60"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(5), adjust=False).over("symbol").alias("ema5"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(10), adjust=False).over("symbol").alias("ema10"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(20), adjust=False).over("symbol").alias("ema20"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(30), adjust=False).over("symbol").alias("ema30"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(60), adjust=False).over("symbol").alias("ema60"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(12), adjust=False).over("symbol").alias("_ema12"),
        pl.col("close").ewm_mean(alpha=_ema_alpha(26), adjust=False).over("symbol").alias("_ema26"),
        pl.col("close").rolling_std(20).over("symbol").alias("_boll_std"),
        pl.col("low").rolling_min(9).over("symbol").alias("_kdj_ln"),
        pl.col("high").rolling_max(9).over("symbol").alias("_kdj_hn"),
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - prev_close).abs(),
            (pl.col("low") - prev_close).abs(),
        ).alias("_tr"),
        pl.col("volume").rolling_mean(5).over("symbol").alias("vol_ma5"),
        pl.col("volume").rolling_mean(10).over("symbol").alias("vol_ma10"),
        # 标准量比分母：前 5 日均量（不含当天）
        pl.col("volume").shift(1).rolling_mean(5).over("symbol").alias("_vol_ma5_prev"),
        pl.col("close").rolling_max(60).over("symbol").alias("high_60d"),
        pl.col("close").rolling_min(60).over("symbol").alias("low_60d"),
    ])

    # Pass 2: MACD + BOLL 上/下轨（依赖 Pass 1 基础列）
    df = df.with_columns([
        (pl.col("_ema12") - pl.col("_ema26")).alias("macd_dif"),
        (pl.col("ma20") + 2 * pl.col("_boll_std")).alias("boll_upper"),
        (pl.col("ma20") - 2 * pl.col("_boll_std")).alias("boll_lower"),
    ]).with_columns([
        pl.col("macd_dif").ewm_mean(alpha=_ema_alpha(9), adjust=False).over("symbol").alias("macd_dea"),
    ]).with_columns([
        ((pl.col("macd_dif") - pl.col("macd_dea")) * 2).alias("macd_hist"),
    ])

    # Pass 3: KDJ（RSV 先做 EMA(3) 得 K，再 EMA(3) 得 D）
    _kdj_rsv = (
        100 * (pl.col("close") - pl.col("_kdj_ln"))
        / (pl.col("_kdj_hn") - pl.col("_kdj_ln")).fill_null(1e-12)
    )
    df = df.with_columns([
        _kdj_rsv.ewm_mean(alpha=1.0 / 3, adjust=False).over("symbol").alias("kdj_k"),
    ]).with_columns([
        pl.col("kdj_k").ewm_mean(alpha=1.0 / 3, adjust=False).over("symbol").alias("kdj_d"),
    ]).with_columns([
        (3 * pl.col("kdj_k") - 2 * pl.col("kdj_d")).alias("kdj_j"),
    ])

    # Pass 4: ATR + 量比 + 动量 + 涨跌幅/额 + 振幅 + 年化波动
    df = df.with_columns([
        pl.col("_tr").ewm_mean(alpha=1.0 / 14, adjust=False).over("symbol").alias("atr_14"),
        # 分母必须 > 0 才算量比（2026-10-20 修复）：停牌期 0 量/微小负数残渣
        # 会让复牌日算出 inf/±1e15 级垃圾，单只股票拖垮全市场均值 KPI
        (pl.when(pl.col("_vol_ma5_prev") > 0)
          .then(pl.col("volume") / pl.col("_vol_ma5_prev"))
          .otherwise(None)).alias("vol_ratio_5d"),
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("momentum_5d"),
        (pl.col("close") / pl.col("close").shift(10).over("symbol") - 1).alias("momentum_10d"),
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1).alias("momentum_20d"),
        (pl.col("close") / pl.col("close").shift(30).over("symbol") - 1).alias("momentum_30d"),
        (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1).alias("momentum_60d"),
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("change_pct"),
        (pl.col("close") - pl.col("close").shift(1).over("symbol")).alias("change_amount"),
        pl.when(pl.col("close").shift(1).over("symbol") > 0)
          .then((pl.col("high") - pl.col("low")) / pl.col("close").shift(1).over("symbol"))
          .otherwise(None)
          .alias("amplitude"),
        pl.col("close").pct_change().over("symbol").alias("_daily_pct"),
    ]).with_columns([
        (pl.col("_daily_pct").rolling_std(20).over("symbol") * (252 ** 0.5))
            .alias("annual_vol_20d"),
    ])

    # Pass 5: RSI（Wilder 平滑：alpha = 1/n）
    df = df.with_columns(
        pl.col("close").diff().over("symbol").alias("_delta"),
    ).with_columns([
        pl.when(pl.col("_delta") > 0).then(pl.col("_delta")).otherwise(0.0).alias("_gain"),
        pl.when(pl.col("_delta") < 0).then(-pl.col("_delta")).otherwise(0.0).alias("_loss"),
    ])
    for n in (6, 14, 24):
        a = 1.0 / n
        df = df.with_columns([
            pl.col("_gain").ewm_mean(alpha=a, adjust=False).over("symbol").alias(f"_rsi_avg_gain_{n}"),
            pl.col("_loss").ewm_mean(alpha=a, adjust=False).over("symbol").alias(f"_rsi_avg_loss_{n}"),
        ]).with_columns(
            (100 - 100 / (1 + pl.col(f"_rsi_avg_gain_{n}") /
                         pl.when(pl.col(f"_rsi_avg_loss_{n}") == 0)
                           .then(1e-12)
                           .otherwise(pl.col(f"_rsi_avg_loss_{n}"))
                         )).alias(f"rsi_{n}"),
        )

    return df.drop([c for c in _INDICATOR_TEMP_COLS if c in df.columns])


def compute_signals(df: pl.DataFrame) -> pl.DataFrame:
    """从已有指标列计算原子信号布尔列（输入须先过 `compute_indicators`）。"""
    if df.is_empty():
        return df

    return df.with_columns([
        ((pl.col("ma5") > pl.col("ma20")) &
         (pl.col("ma5").shift(1).over("symbol") <= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma_golden_5_20"),
        ((pl.col("ma5") < pl.col("ma20")) &
         (pl.col("ma5").shift(1).over("symbol") >= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma_dead_5_20"),
        ((pl.col("ma20") > pl.col("ma60")) &
         (pl.col("ma20").shift(1).over("symbol") <= pl.col("ma60").shift(1).over("symbol")))
            .alias("signal_ma_golden_20_60"),
        ((pl.col("macd_dif") > pl.col("macd_dea")) &
         (pl.col("macd_dif").shift(1).over("symbol") <= pl.col("macd_dea").shift(1).over("symbol")))
            .alias("signal_macd_golden"),
        ((pl.col("macd_dif") < pl.col("macd_dea")) &
         (pl.col("macd_dif").shift(1).over("symbol") >= pl.col("macd_dea").shift(1).over("symbol")))
            .alias("signal_macd_dead"),
        ((pl.col("close") > pl.col("ma20")) &
         (pl.col("close").shift(1).over("symbol") <= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma20_breakout"),
        ((pl.col("close") < pl.col("ma20")) &
         (pl.col("close").shift(1).over("symbol") >= pl.col("ma20").shift(1).over("symbol")))
            .alias("signal_ma20_breakdown"),
        ((pl.col("close") > pl.col("ma5")) &
         (pl.col("close").shift(1).over("symbol") <= pl.col("ma5").shift(1).over("symbol")))
            .alias("signal_ma5_breakout"),
        ((pl.col("close") < pl.col("ma5")) &
         (pl.col("close").shift(1).over("symbol") >= pl.col("ma5").shift(1).over("symbol")))
            .alias("signal_ma5_breakdown"),
        ((pl.col("close") > pl.col("ma10")) &
         (pl.col("close").shift(1).over("symbol") <= pl.col("ma10").shift(1).over("symbol")))
            .alias("signal_ma10_breakout"),
        ((pl.col("close") < pl.col("ma10")) &
         (pl.col("close").shift(1).over("symbol") >= pl.col("ma10").shift(1).over("symbol")))
            .alias("signal_ma10_breakdown"),
        (pl.col("close") >= pl.col("high_60d")).alias("signal_n_day_high"),
        (pl.col("close") <= pl.col("low_60d")).alias("signal_n_day_low"),
        (pl.col("close") > pl.col("boll_upper")).alias("signal_boll_breakout_upper"),
        (pl.col("close") < pl.col("boll_lower")).alias("signal_boll_breakdown_lower"),
        (pl.col("vol_ratio_5d") >= 2.0).alias("signal_volume_surge"),
    ])


def compute_limit_signals(df: pl.DataFrame, *, name: str | None = None) -> pl.DataFrame:
    """计算涨跌停相关信号。

    产出：signal_limit_up / consecutive_limit_ups / signal_limit_down /
    consecutive_limit_downs / signal_limit_down_recovery（跌停翘板）/
    signal_broken_limit_up（炸板）。

    输入必须包含: symbol, date, raw_close, raw_high, raw_low, open, high, low,
    close（后四者是前复权价，用于除权日判定）。

    相对 tickflow 原版的差异：原版会 JOIN instruments 维表取**权威**涨跌停价
    （维表日期与行情日期一致时才用），astock-hub 没有这张维表，因此**一律用
    按交易所规则自算的理论价**（`price_limits`）。ST 判定改为调用方传入 `name`。
    """
    if df.is_empty():
        return df

    required = {"raw_close", "raw_high", "raw_low", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"compute_limit_signals 缺少列: {sorted(missing)}")

    # 前一日参考收盘价（交易所涨跌停基准价）：仅当复权因子发生变化（除权除息）
    # 时用前复权昨收，否则用原始昨收，以避免浮点精度误差。
    _adj_today = pl.col("close") / pl.col("raw_close")
    _adj_yesterday = pl.col("close").shift(1).over("symbol") / pl.col("raw_close").shift(1).over("symbol")
    _adj_changed = (_adj_today - _adj_yesterday).abs() > 1e-6
    df = df.with_columns(
        pl.when(_adj_changed)
        .then(pl.col("close").shift(1).over("symbol"))          # 除权日：前复权昨收
        .otherwise(pl.col("raw_close").shift(1).over("symbol"))  # 平常：原始昨收
        .alias("_prev_raw_close")
    )

    is_risk_warning = pl.lit("ST" in str(name or "").upper())
    df = df.with_columns(
        polars_price_limit_pct(pl.col("symbol"), pl.col("date"), is_risk_warning)
        .alias("_limit_pct")
    ).with_columns([
        polars_limit_price(pl.col("_prev_raw_close"), pl.col("_limit_pct"), up=True)
            .alias("_effective_limit_up"),
        polars_limit_price(pl.col("_prev_raw_close"), pl.col("_limit_pct"), up=False)
            .alias("_effective_limit_down"),
    ])

    # 涨/跌停：以原始价与理论价比较，容差 0.005（半分钱）吸收浮点误差
    _valid_base = (pl.col("_prev_raw_close").is_not_null()
                   & (pl.col("_prev_raw_close") > 0))
    df = df.with_columns([
        pl.when(_valid_base & (pl.col("raw_close") > 0))
          .then(pl.col("raw_close") >= (pl.col("_effective_limit_up") - 0.005))
          .otherwise(None).cast(pl.Boolean).alias("signal_limit_up"),
        pl.when(_valid_base & (pl.col("raw_close") > 0))
          .then(pl.col("raw_close") <= (pl.col("_effective_limit_down") + 0.005))
          .otherwise(None).cast(pl.Boolean).alias("signal_limit_down"),
    ])

    # 连续板数：按"信号为假"分组后累计连续为真的天数
    for suffix, sig in (("ups", "signal_limit_up"), ("downs", "signal_limit_down")):
        df = df.with_columns(
            (~pl.col(sig).fill_null(False)).cast(pl.UInt32).cum_sum()
            .over("symbol").alias(f"_grp_{suffix}")
        ).with_columns(
            pl.col(sig).cast(pl.UInt32).cum_sum()
            .over("symbol", f"_grp_{suffix}").cast(pl.UInt32)
            .alias(f"consecutive_limit_{suffix}")
        ).with_columns(
            pl.when(pl.col(sig).fill_null(False))
              .then(pl.col(f"consecutive_limit_{suffix}"))
              .otherwise(0).cast(pl.UInt32).alias(f"consecutive_limit_{suffix}")
        )

    # 跌停翘板：当日最低价曾触及跌停 + 最终没跌停 + 收阳
    df = df.with_columns(
        pl.when(_valid_base & (pl.col("raw_low") > 0))
          .then(
              (~pl.col("signal_limit_down").fill_null(False))
              & (pl.col("raw_low") <= pl.col("_effective_limit_down") + 0.005)
              & (pl.col("close") > pl.col("open"))
          ).otherwise(None).cast(pl.Boolean).alias("signal_limit_down_recovery")
    )

    # 炸板：最高价曾触及涨停价 + 最终没封住
    df = df.with_columns(
        pl.when(_valid_base & (pl.col("raw_high") > 0))
          .then(
              (~pl.col("signal_limit_up").fill_null(False))
              & (pl.col("raw_high") >= pl.col("_effective_limit_up") - 0.005)
          ).otherwise(None).cast(pl.Boolean).alias("signal_broken_limit_up")
    )

    cleanup = ["_prev_raw_close", "_limit_pct",
               "_effective_limit_up", "_effective_limit_down",
               "_grp_ups", "_grp_downs"]
    return df.drop([c for c in cleanup if c in df.columns])


def filter_halt_days(df: pl.DataFrame) -> pl.DataFrame:
    """过滤停牌日。

    停牌日的 open/high 必然为 0（无集合竞价）。注意 close 可能被数据源填充为
    前收盘价而非 0，因此不能用"OHLC 全零"判断，否则会漏过这类停牌记录，污染
    MA/ATR 等指标；对零成交量的历史数据用零成交量+零成交额作为兼容判据。

    例外：如果 raw_close 有效（非空且>0），说明有数据，不应过滤。
    """
    if df.is_empty() or "open" not in df.columns or "high" not in df.columns:
        return df
    halted = (pl.col("open") == 0) & (pl.col("high") == 0)
    if "volume" in df.columns and "amount" in df.columns:
        halted = halted | ((pl.col("volume") == 0) & (pl.col("amount") == 0))
    # 如果有 raw_close 列，有有效 raw_close 的行不视为停牌
    if "raw_close" in df.columns:
        has_valid_raw = pl.col("raw_close").is_not_null() & (pl.col("raw_close") > 0)
        halted = halted & ~has_valid_raw
    return df.filter(~halted)


def compute_all(df: pl.DataFrame, *, name: str | None = None) -> pl.DataFrame:
    """一站式：指标 + 信号（+ 有原始价时补涨跌停信号）。"""
    df = compute_indicators(df)
    df = compute_signals(df)
    if {"raw_close", "raw_high", "raw_low"} <= set(df.columns):
        df = compute_limit_signals(df, name=name)

    float_cols = [c for c in df.columns if df[c].dtype.is_float()]
    if float_cols:
        df = df.with_columns([
            pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
              .then(None).otherwise(pl.col(c)).alias(c)
            for c in float_cols
        ])
    return df
