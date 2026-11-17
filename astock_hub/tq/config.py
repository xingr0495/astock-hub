# astock_hub/tq/config.py
"""[tq] 交易通道配置：config.toml 的 [tq] 段 + 默认值。"""
from __future__ import annotations

from pathlib import Path

from ..config import PROJECT_ROOT

_DEFAULTS = {
    "enabled": False,
    "base_url": "http://127.0.0.1:17709/",
    "timeout_sec": 8,
    "single_order_max_amount": 20000,   # 单笔金额上限（元）
    "daily_max_orders": 10,             # 每日委托笔数上限（账本计数）
    "price_slip_pct": 0.02,             # 限价相对现价最大偏离
}


_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}


def _parse_bool(v) -> bool:
    """严格布尔解析（fail-closed）。

    bool 原样；字符串按白名单（大小写/空白不敏感）；**其他类型一律 False**——
    bool("false") 为 True 是主开关 fail-open 的经典坑，绝不能让字符串/数字
    值意外把通道打开。
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE_STRINGS:
            return True
        if s in _FALSE_STRINGS:
            return False
    return False


def load_tq_config(cfg: dict) -> dict:
    """从 hub 总配置取 [tq] 段，合并默认值，派生数据路径。"""
    merged = dict(_DEFAULTS)
    merged.update(cfg.get("tq") or {})
    merged["enabled"] = _parse_bool(merged["enabled"])
    data_dir = PROJECT_ROOT / "data" / "tq"
    merged["kill_flag"] = data_dir / "kill.flag"
    merged["ledger_dir"] = data_dir / "ledger"
    return merged
