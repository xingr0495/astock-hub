# astock_hub/nav_config.py
"""侧栏栏目自定义的服务端存储（跨设备同步；localStorage 降级为前端兜底）。

存储：data/nav_config.json（锚定包根目录，同 watchlist 惯例）。
载荷形状固定为 {"order": [str...], "hidden": [str...]}，坏载荷拒绝不落盘。
"""
from __future__ import annotations

import json
from pathlib import Path

_FILE = Path(__file__).resolve().parents[1] / "data" / "nav_config.json"
_MAX_KEYS = 100  # 顺序表上限（NAV 现有 13 项，留足余量防垃圾灌入）

_DEFAULT = {"order": [], "hidden": []}


def _load() -> dict:
    try:
        loaded = json.loads(_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            order = loaded.get("order")
            hidden = loaded.get("hidden")
            if isinstance(order, list) and isinstance(hidden, list):
                return {"order": [x for x in order if isinstance(x, str)][: _MAX_KEYS * 2],
                        "hidden": [x for x in hidden if isinstance(x, str)][: _MAX_KEYS * 2]}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    return dict(_DEFAULT)


def _save(data: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_FILE)


def get_state() -> dict:
    return _load()


def save_state(payload: dict) -> dict:
    """校验并保存。形状不对抛 ValueError（端点层转 400）。"""
    if not isinstance(payload, dict):
        raise ValueError("payload must be object")
    order = payload.get("order")
    hidden = payload.get("hidden")
    if not isinstance(order, list) or not isinstance(hidden, list):
        raise ValueError("order/hidden must be arrays")
    if len(order) > _MAX_KEYS or len(hidden) > _MAX_KEYS:
        raise ValueError("too many keys")
    if not all(isinstance(x, str) for x in order + hidden):
        raise ValueError("keys must be strings")
    data = {"order": list(order), "hidden": list(hidden)}
    _save(data)
    return data
