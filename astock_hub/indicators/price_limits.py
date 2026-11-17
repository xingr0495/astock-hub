"""A 股涨跌停价规则（从 tickflow `backend/app/price_limits.py` 移植）。

hithink 本地库没有涨跌停价表，所以这里全部按交易所规则自算：
板块差异（主板 10% / 创业科创 20% / 北交所 30%）、ST（主板 5%，2026-07-06 起
主板 ST 提到 10%）、以及"整数分半进位"的官方四舍五入（Python 内置 round 是
银行家舍入，会差一分钱，必须用这套整数算术）。

原文件里的 numpy 矩阵版本（供回测批量构造时间×标的矩阵）未移植——那是
numba 回测路径专用，astock-hub 暂时不需要，也就不必引入 numpy。
"""

from __future__ import annotations

from datetime import date

import polars as pl

# 主板 ST 涨跌幅由 5% 调整为 10% 的生效日（交易所规则）
MAIN_BOARD_ST_LIMIT_CHANGE_DATE = date(2026, 7, 6)

MAIN_BOARD_LIMIT = 0.10
LEGACY_MAIN_BOARD_ST_LIMIT = 0.05
GROWTH_BOARD_LIMIT = 0.20
BEIJING_BOARD_LIMIT = 0.30


def is_risk_warning_name(name: str | None) -> bool:
    """名称里含 ST 即视为风险警示股（含 *ST）。"""
    return "ST" in str(name or "").upper()


def board_limit_pct(symbol: str) -> float:
    """按板块给出基础涨跌幅限制（不含 ST 例外）。"""
    if symbol.endswith(".BJ"):
        return BEIJING_BOARD_LIMIT
    if symbol.startswith(("300", "301", "688", "689")):
        return GROWTH_BOARD_LIMIT
    return MAIN_BOARD_LIMIT


def price_limit_pct(
    symbol: str,
    trade_date: date,
    *,
    is_risk_warning: bool = False,
) -> float:
    """单值版：某标在某日的有效涨跌幅限制。"""
    base = board_limit_pct(symbol)
    if (
        base == MAIN_BOARD_LIMIT
        and is_risk_warning
        and trade_date < MAIN_BOARD_ST_LIMIT_CHANGE_DATE
    ):
        return LEGACY_MAIN_BOARD_ST_LIMIT
    return base


def polars_price_limit_pct(
    symbol: pl.Expr,
    trade_date: pl.Expr,
    is_risk_warning: pl.Expr,
) -> pl.Expr:
    """向量化版有效涨跌幅限制（供 DataFrame 批量计算）。"""
    is_growth = symbol.str.starts_with("300") | symbol.str.starts_with("301")
    is_star = symbol.str.starts_with("688") | symbol.str.starts_with("689")
    is_beijing = symbol.str.ends_with(".BJ")
    is_non_main = is_growth | is_star | is_beijing
    base = (
        pl.when(is_growth | is_star).then(GROWTH_BOARD_LIMIT)
        .when(is_beijing).then(BEIJING_BOARD_LIMIT)
        .otherwise(MAIN_BOARD_LIMIT)
    )
    # ST 的 5% 只对主板、且仅在该规则调整日之前成立
    legacy_main_st = (
        is_risk_warning.fill_null(False)
        & ~is_non_main
        & (trade_date < pl.lit(MAIN_BOARD_ST_LIMIT_CHANGE_DATE))
    )
    return (
        pl.when(legacy_main_st).then(LEGACY_MAIN_BOARD_ST_LIMIT)
        .otherwise(base)
        .cast(pl.Float64)
    )


def polars_is_risk_warning_name(name: pl.Expr) -> pl.Expr:
    """向量化版风险警示股判定。"""
    return name.fill_null("").str.to_uppercase().str.contains("ST", literal=True)


def limit_price(previous: float, limit_pct: float, *, up: bool) -> float:
    """单值版涨/跌停价。整数分算术，避免浮点误差导致的差一分钱。"""
    sign = 1 if up else -1
    numerator = round((1 + sign * limit_pct) * 100)
    cents = int((previous * 100) + 0.5)  # 昨收也按"四舍五入到分"处理
    return ((cents * numerator + 50) // 100) / 100


def polars_limit_price(previous: pl.Expr, limit_pct: pl.Expr, *, up: bool) -> pl.Expr:
    """向量化版涨/跌停价（与 `limit_price` 同一套整数分算术）。"""
    sign = 1 if up else -1
    numerator = ((1 + sign * limit_pct) * 100).round(0).cast(pl.Int64)
    cents = (previous * 100 + 0.5).floor().cast(pl.Int64)
    return ((cents * numerator + 50) // 100) / 100
