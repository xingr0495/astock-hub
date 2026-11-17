"""选股策略引擎：在 as_of 日筛出满足入场条件的股票，并按策略 scoring 打分排名。

打分口径逐字对齐 tickflow `strategy/engine.py._apply_scoring`：
1. 每个 scoring 因子在**当日截面**做 min-max 归一（(v-min)/(max-min)），范围为 0（全相同）
   时给 0.5；direction=low 的因子取 1-normalized；
2. 权重归一（除以可用因子的权重和——某个因子帧里缺失时自动剔除、重算权重）；
3. 加权求和 ×100 → score（0-100）。

选股器只算一根 bar（as_of），不回测、不做退出。
"""

from __future__ import annotations

import logging

import polars as pl

from .base import StrategyDef
from ..strategy_factors import add_strategy_factors
from .group_doc1 import add_market_state as _doc1_add_market_state
from .group_doc1 import _nt_params as _doc1_nt_params

logger = logging.getLogger(__name__)


def _apply_basic_filter(frame: pl.DataFrame, bf: dict) -> pl.DataFrame:
    """应用基础过滤条件（与 tickflow 对齐）。

    过滤条件：
    - 价格范围：close >= price_min 且 close <= price_max
    - 成交额：amount >= amount_min
    - 排除 ST：name 不包含 ST/*ST/退
    - 板块白名单：symbol 前缀匹配
    """
    if frame.is_empty():
        return frame

    exprs = []

    # 价格过滤
    price_min = bf.get("price_min")
    price_max = bf.get("price_max")
    if price_min is not None and "close" in frame.columns:
        exprs.append(pl.col("close") >= price_min)
    if price_max is not None and "close" in frame.columns:
        exprs.append(pl.col("close") <= price_max)

    # 成交额过滤
    amount_min = bf.get("amount_min")
    if amount_min is not None and "amount" in frame.columns:
        exprs.append(pl.col("amount") >= amount_min)

    # 排除 ST
    if bf.get("exclude_st", True) and "name" in frame.columns:
        exprs.append(~pl.col("name").str.contains("(?i)ST|\\*ST|退"))

    # 板块白名单
    boards = bf.get("boards")
    if boards and "symbol" in frame.columns:
        board_exprs = []
        for board in boards:
            if board == "沪主板":
                board_exprs.append(pl.col("symbol").str.starts_with("6"))
            elif board == "深主板":
                board_exprs.append(
                    pl.col("symbol").str.starts_with("0") |
                    pl.col("symbol").str.starts_with("001")
                )
            elif board == "创业板":
                board_exprs.append(
                    pl.col("symbol").str.starts_with("300") |
                    pl.col("symbol").str.starts_with("301")
                )
            elif board == "科创板":
                board_exprs.append(
                    pl.col("symbol").str.starts_with("688") |
                    pl.col("symbol").str.starts_with("689")
                )
            elif board == "北交所":
                board_exprs.append(pl.col("symbol").str.ends_with(".BJ"))
        if board_exprs:
            combined = board_exprs[0]
            for e in board_exprs[1:]:
                combined = combined | e
            exprs.append(combined)

    if not exprs:
        return frame

    # 所有条件取 AND
    combined = exprs[0]
    for e in exprs[1:]:
        combined = combined & e

    return frame.filter(combined)


def _apply_vol_ratio_time_factor(frame: pl.DataFrame) -> pl.DataFrame:
    """应用盘中量比时间因子（与 tickflow 对齐）。

    公式：vol_ratio_5d = (volume * time_factor) / vol_ma5_prev
    time_factor = 240 / elapsed_minutes（盘中），1.0（盘后）
    """
    try:
        from ..market.sessions import time_factor_for_vol_ratio
    except ImportError:
        # 导入失败时返回原帧（避免循环导入）
        return frame

    if frame.is_empty() or "volume" not in frame.columns:
        return frame

    # 检查是否需要盘中折算
    tf = time_factor_for_vol_ratio()
    if tf <= 1.0:
        # 盘后或无效时间，不折算
        return frame

    # 盘中：折算 vol_ratio_5d
    if "vol_ratio_5d" in frame.columns and "_vol_ma5_prev" in frame.columns:
        frame = frame.with_columns(
            (pl.col("volume") * tf / pl.col("_vol_ma5_prev")).alias("vol_ratio_5d")
        )

    return frame


def _normalize_param_overrides(strategy: StrategyDef, overrides: dict | None) -> dict:
    p = strategy.default_params()
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                p[k] = v
    return p


def _ensure_market_state(frame: pl.DataFrame, strategy: "StrategyDef") -> pl.DataFrame:
    """手册策略（group_doc1）依赖市场级列；只给这类策略补，避免白算。"""
    if not strategy.id.startswith("doc1_"):
        return frame
    if "m_state" not in frame.columns:
        frame = _doc1_add_market_state(frame)
    # 国家队确认版额外注入资金面确认列（按策略参数定口径与阈值）
    if strategy.id.startswith("doc1_nt_"):
        params = strategy.default_params()
        frame = _doc1_nt_params(frame, params)
    return frame


def _entry_expr(strategy: StrategyDef, params: dict) -> pl.Expr:
    expr = strategy.entry(pl.DataFrame(), params)
    # entry 可能产出纯 Python True（无条件），转成布尔常量
    if expr is True or expr is pl.lit(True):
        return pl.lit(True)
    return expr.cast(pl.Boolean)


class MissingFactorError(Exception):
    """入场条件引用了帧里不存在的列（如 high_turnover 需要 turnover_rate/流通股本）。"""

    def __init__(self, missing: str):
        self.missing = missing
        super().__init__(missing)


def run_strategy(frame: pl.DataFrame, strategy: StrategyDef, *,
                 overrides: dict | None = None, limit: int | None = None,
                 as_of=None) -> pl.DataFrame:
    """在 frame（多日全市场日K、含指标列）上跑一个策略。

    entry 必须在**多日帧**上求值——条件里可能有 `.shift(1)`（取昨日的均线/高点等），
    若调用方先截成 as_of 单日帧，shift 必为空、会漏掉所有用到昨日数据的策略。
    求值后只取 as_of 当日的命中行（as_of=None 时取帧内最新日），再截面打分排名。

    返回命中且打过分的行（按 score 降序、截断 limit），每行带 score。
    """
    if frame.is_empty():
        return frame

    frame = add_strategy_factors(frame)
    frame = _ensure_market_state(frame, strategy)
    params = _normalize_param_overrides(strategy, overrides)
    target = as_of if as_of is not None else frame["date"].max()

    # 应用基础过滤（与 tickflow 对齐）
    bf = dict(strategy.basic_filter)
    if overrides and overrides.get("basic_filter"):
        bf.update(overrides["basic_filter"])

    entry = _entry_expr(strategy, params)
    try:
        matched = frame.filter(entry).fill_nan(None)
    except pl.exceptions.ColumnNotFoundError as exc:
        # 缺列（如 turnover_rate）：透给调用方给"数据不可用"提示，不崩、不误报 0
        raise MissingFactorError(str(exc).split("\n")[0]) from exc

    # 应用基础过滤条件
    matched = _apply_basic_filter(matched, bf)

    # entry 已在完整历史上求值（shift 有效）；这里只保留 as_of 当日的命中
    hits = matched.filter(pl.col("date") == target)
    if hits.is_empty():
        return hits

    # 迷你走势图数据：从原始多日帧按 symbol 收集截至 as_of 的近 20 日收盘（一次聚合，
    # 避免对每只命中股单独拉K线）。帧按 symbol,date 升序，tail 取最近20根。
    pre = frame.filter(pl.col("date") <= target)
    spark = (
        pre.sort(["symbol", "date"])
        .group_by("symbol")
        .agg(pl.col("close").tail(20).alias("_spark_close"))
    )
    hits = hits.join(spark, on="symbol", how="left")

    # 应用盘中量比时间因子
    hits = _apply_vol_ratio_time_factor(hits)

    # 计算虚拟评分因子
    hits = _compute_virtual_scoring_factors(hits)

    hits = _apply_scoring(hits, strategy.scoring)
    hits = hits.sort("score", descending=strategy.descending, nulls_last=True)
    n = limit or strategy.limit
    return hits.head(n) if n else hits


def _apply_scoring(frame: pl.DataFrame, weights: dict[str, float]) -> pl.DataFrame:
    """截面 min-max 归一 → 加权 → score(0-100)。与 tickflow 同口径。"""
    # 帧里存在且非全空的因子才参与；缺失因子剔除后权重重归一
    usable = [(name, float(w)) for name, w in weights.items()
              if w and name in frame.columns]
    total_w = sum(w for _, w in usable)
    if total_w <= 0:
        # 没有任何打分因子可用：给中性分，保持可排名（顺序退化为帧原序）
        return frame.with_columns(pl.lit(50.0).alias("score"))

    parts: list[pl.Expr] = []
    for name, w in usable:
        v = pl.col(name)
        col_min = v.min()
        col_range = v.max() - col_min
        norm = pl.when(col_range > 0).then((v - col_min) / col_range) \
                 .otherwise(pl.lit(0.5))
        parts.append(norm * (w / total_w))

    score = parts[0]
    for p in parts[1:]:
        score = score + p
    return frame.with_columns((score * 100).alias("score"))


def _compute_virtual_scoring_factors(frame: pl.DataFrame) -> pl.DataFrame:
    """计算虚拟评分因子（与 tickflow 对齐）。

    这些因子不是 DataFrame 中已有的列，而是通过表达式即时计算。
    用于丰富评分维度，提高选股效果。
    """
    if frame.is_empty():
        return frame

    # 检查必要的列是否存在
    required_cols = ["close", "boll_lower", "boll_upper", "volume", "amount"]
    if not all(col in frame.columns for col in required_cols):
        return frame

    # 计算虚拟因子
    exprs = []

    # 1. boll_position: (close - boll_lower) / (boll_upper - boll_lower)
    if "boll_lower" in frame.columns and "boll_upper" in frame.columns:
        boll_range = pl.col("boll_upper") - pl.col("boll_lower")
        exprs.append(
            pl.when(boll_range > 0)
              .then((pl.col("close") - pl.col("boll_lower")) / boll_range)
              .otherwise(pl.lit(0.5))
              .alias("boll_position")
        )

    # 2. log_amount: log(amount + 1)
    if "amount" in frame.columns:
        exprs.append(
            pl.when(pl.col("amount") >= 0)
              .then((pl.col("amount") + 1).log())
              .otherwise(None)
              .alias("log_amount")
        )

    # 3. ma20_bias: (close / ma20) - 1
    if "ma20" in frame.columns:
        exprs.append(
            pl.when(pl.col("ma20") > 0)
              .then((pl.col("close") / pl.col("ma20")) - 1)
              .otherwise(None)
              .alias("ma20_bias")
        )

    # 4. close_position: (close - low) / (high - low)
    if "low" in frame.columns and "high" in frame.columns:
        price_range = pl.col("high") - pl.col("low")
        exprs.append(
            pl.when(price_range > 0)
              .then((pl.col("close") - pl.col("low")) / price_range)
              .otherwise(pl.lit(0.5))
              .alias("close_position")
        )

    # 5. atr_pct: atr_14 / close
    if "atr_14" in frame.columns:
        exprs.append(
            pl.when(pl.col("close") > 0)
              .then(pl.col("atr_14") / pl.col("close"))
              .otherwise(None)
              .alias("atr_pct")
        )

    # 6. distance_to_high_60d: (close / high_60d) - 1
    if "high_60d" in frame.columns:
        exprs.append(
            pl.when(pl.col("high_60d") > 0)
              .then((pl.col("close") / pl.col("high_60d")) - 1)
              .otherwise(None)
              .alias("distance_to_high_60d")
        )

    # 7. distance_from_low_60d: (close / low_60d) - 1
    if "low_60d" in frame.columns:
        exprs.append(
            pl.when(pl.col("low_60d") > 0)
              .then((pl.col("close") / pl.col("low_60d")) - 1)
              .otherwise(None)
              .alias("distance_from_low_60d")
        )

    # 8. kdj_kd_diff: kdj_k - kdj_d
    if "kdj_k" in frame.columns and "kdj_d" in frame.columns:
        exprs.append(
            (pl.col("kdj_k") - pl.col("kdj_d")).alias("kdj_kd_diff")
        )

    if exprs:
        frame = frame.with_columns(exprs)

    return frame


def _apply_scoring_by_date(frame: pl.DataFrame, weights: dict[str, float]) -> pl.DataFrame:
    """按日截面 min-max 打分（_apply_scoring 的全历史版；单日口径见 run_strategy）。"""
    usable = [(name, float(w)) for name, w in weights.items()
              if w and name in frame.columns]
    total_w = sum(w for _, w in usable)
    if total_w <= 0:
        return frame.with_columns(pl.lit(50.0).alias("score"))
    parts: list[pl.Expr] = []
    for name, w in usable:
        v = pl.col(name)
        vmin = v.min().over("date")
        vrange = v.max().over("date") - vmin
        norm = pl.when(vrange > 0).then((v - vmin) / vrange).otherwise(pl.lit(0.5))
        parts.append(norm * (w / total_w))
    score = parts[0]
    for p in parts[1:]:
        score = score + p
    return frame.with_columns((score * 100).alias("score"))


def entry_hits_all_dates(frame: pl.DataFrame, strategy: StrategyDef, *,
                         overrides: dict | None = None) -> pl.DataFrame:
    """在多日全市场帧上跑入场条件，返回**全部日期**的命中行并按日截面打分。

    与 run_strategy 的差别：不截 as_of、不截 limit、不算迷你走势图——
    回测要完整历史信号矩阵。score 按 date 分组 min-max（与 run_strategy 单日同口径）。
    """
    if frame.is_empty():
        return frame
    frame = add_strategy_factors(frame)
    frame = _ensure_market_state(frame, strategy)
    params = _normalize_param_overrides(strategy, overrides)
    bf = dict(strategy.basic_filter)
    if overrides and overrides.get("basic_filter"):
        bf.update(overrides["basic_filter"])
    entry = _entry_expr(strategy, params)
    try:
        matched = frame.filter(entry).fill_nan(None)
    except pl.exceptions.ColumnNotFoundError as exc:
        raise MissingFactorError(str(exc).split("\n")[0]) from exc
    matched = _apply_basic_filter(matched, bf)
    if matched.is_empty():
        return matched
    matched = _compute_virtual_scoring_factors(matched)
    return _apply_scoring_by_date(matched, strategy.scoring)
