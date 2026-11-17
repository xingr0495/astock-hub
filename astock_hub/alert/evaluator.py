"""告警评估引擎：盘中轮询评估所有规则"""

from __future__ import annotations

import logging
import math
import time

from .rules import load_rules
from ..adapters.hithink import HithinkAdapter
from ..config import load_config
from ..store.market_data import symbol_names

logger = logging.getLogger(__name__)

_last_eval_time: float = 0.0
_EVAL_INTERVAL = 30  # 每 30 秒评估一次


def _finite(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _get_price(hithink: HithinkAdapter, symbol: str) -> float | None:
    """获取当前价格（快照）"""
    snap = hithink.realtime_snapshot([symbol])
    if snap.status == "ok" and snap.data:
        items = snap.data.get("item") or []
        if items:
            return _finite(items[0].get("last_price"))
    return None


def evaluate_alerts(cfg: dict = None, force: bool = False) -> list[dict]:
    """评估所有告警规则，返回触发列表

    force=True 强制评估（不受 interval 限制）
    """
    global _last_eval_time

    now = time.time()
    if not force and (now - _last_eval_time) < _EVAL_INTERVAL:
        return []
    _last_eval_time = now

    if cfg is None:
        cfg = load_config()

    rules = load_rules()
    triggered = []

    hithink = HithinkAdapter(cfg.get("hithink", {}))
    name_map = {}
    try:
        names_df = symbol_names(cfg)
        name_map = {r["symbol"]: r["name"] for r in names_df.to_dicts()}
    except Exception:
        pass

    for rule in rules:
        if not rule.get("enabled"):
            continue

        symbol = rule.get("symbol", "")
        if not symbol:
            continue

        price = _get_price(hithink, symbol)
        if price is None:
            continue

        name = rule.get("name") or name_map.get(symbol, symbol)
        alert_type = rule.get("type", "")
        threshold = rule.get("threshold")

        # 评估触发条件
        triggered_msg = None
        if alert_type == "price_above" and threshold is not None and price > threshold:
            triggered_msg = f"价格突破 {threshold}：当前 {price:.2f}"
        elif alert_type == "price_below" and threshold is not None and price < threshold:
            triggered_msg = f"价格跌破 {threshold}：当前 {price:.2f}"
        elif alert_type == "price_change_up" and threshold is not None:
            change = _get_change_pct(hithink, symbol)
            if change is not None and change > threshold:
                triggered_msg = f"涨幅超 {threshold}%：当前 {change:+.2f}%"
        elif alert_type == "price_change_down" and threshold is not None:
            change = _get_change_pct(hithink, symbol)
            if change is not None and change < -abs(threshold):
                triggered_msg = f"跌幅超 {threshold}%：当前 {change:+.2f}%"

        if triggered_msg:
            triggered.append({
                "rule": rule,
                "symbol": symbol,
                "name": name,
                "message": triggered_msg,
                "price": price,
                "timestamp": datetime.now().isoformat(),
            })

    return triggered


def _get_change_pct(hithink: HithinkAdapter, symbol: str) -> float | None:
    snap = hithink.realtime_snapshot([symbol])
    if snap.status == "ok" and snap.data:
        items = snap.data.get("item") or []
        if items:
            return _finite(items[0].get("price_change_ratio_pct"))
    return None


from datetime import datetime
