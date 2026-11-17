"""策略定义的标准接口。

P2 把 tickflow 的 27 个内置选股策略改写成**声明式**：每个策略是一个 StrategyDef，
核心是 `entry(frame, params) -> polars 布尔表达式`（在某根日K上该股是否满足入场条件），
加 `scoring`（字段→权重，引擎做截面 min-max 打分）。

与 tickflow 的区别：tickflow 用 numpy MarketDataMatrix + 回测引擎（含退出/止损/持仓），
astock-hub 的选股器**只需要 as_of 当日的入场名单与排名**，不回测，因此：
- 入场条件逐字翻译为 polars 表达式（必须与 tickflow 的 matrix 布尔逻辑一致）；
- 不搬 backtest.matrix、退出信号、止损、MAX_HOLD_DAYS。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import polars as pl

# entry(frame, params) -> 布尔表达式。
# frame 是单只/全市场日K帧（已物化全部指标与辅助因子列），按行求值。
EntryFn = Callable[[pl.DataFrame, dict], pl.Expr]


# 默认基础过滤阈值（与 tickflow 对齐）
DEFAULT_BASIC_FILTER: dict = {
    "price_min": 3,            # 最低价格 3 元
    "price_max": 300,          # 最高价格 300 元
    "market_cap_min": 10e8,    # 最低总市值 10 亿
    "amount_min": 0.2e8,       # 最低成交额 2000 万
    "exclude_st": True,        # 排除 ST / *ST / 退市
    "boards": ["沪主板", "深主板", "创业板", "科创板", "北交所"],  # 板块白名单
}


@dataclass
class StrategyDef:
    id: str
    name: str
    description: str
    entry: EntryFn
    scoring: dict[str, float]
    tags: list[str] = field(default_factory=list)
    params: list[dict] = field(default_factory=list)
    warmup: int = 60          # 需要的指标预热行（算入场前帧里要多留）
    limit: int = 100          # 默认返回条数
    descending: bool = True
    basic_filter: dict = field(default_factory=lambda: dict(DEFAULT_BASIC_FILTER))
    stop_loss: float = -0.06  # 止损线（默认 -6%）
    max_hold_days: int = 15   # 最大持有天数
    exit_signal_ids: list[str] = field(default_factory=list)  # 退出信号列名

    def default_params(self) -> dict:
        """从 params 定义提取默认值。"""
        out = {}
        for p in self.params:
            if "id" in p and "default" in p:
                out[p["id"]] = p["default"]
        return out
