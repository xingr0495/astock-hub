"""选股策略注册表：汇总全部内置 StrategyDef。

tickflow 共 26 个 builtin；factor_rank_research 为 research_only、依赖独立 score
builder，暂不纳入，故这里 25 个。
"""

from __future__ import annotations

from .base import StrategyDef
from .engine import run_strategy
from .group_a import STRATEGIES as A
from .group_b import STRATEGIES as B
from .group_c import STRATEGIES as C
from .group_doc1 import STRATEGIES as DOC1
from .group_examples import active_limit_gene, ma_golden_cross

# 顺序：范例 2 → A 8 → B 8 → C 7 → DOC1 2（2026-10-27）
REGISTRY: dict[str, StrategyDef] = {
    s.id: s for s in ([ma_golden_cross, active_limit_gene] + A + B + C + DOC1)
}


def list_strategies() -> list[StrategyDef]:
    return list(REGISTRY.values())


def get_strategy(strategy_id: str) -> StrategyDef:
    if strategy_id not in REGISTRY:
        raise KeyError(f"未知策略 {strategy_id}（可用：{sorted(REGISTRY)}）")
    return REGISTRY[strategy_id]


__all__ = ["REGISTRY", "StrategyDef", "list_strategies", "get_strategy", "run_strategy"]
