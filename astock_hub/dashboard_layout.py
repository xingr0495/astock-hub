# -*- coding: utf-8 -*-
"""实时看板区块布局（显隐 + 排序）存取（2026-11-07 用户需求"实时看盘窗口可以自定义调整"）。

与侧栏栏目自定义（nav_config）同款思路：全局一份配置、管理员编辑、全员生效。
默认顺序与看板历史渲染顺序一致，新增区块自动追加到末尾不丢项。
"""
from __future__ import annotations

import json
from pathlib import Path

# id 必须与前端 sections 一一对应（改这里要同步 frontend lib/dashboard-layout.ts）
SECTIONS: list[str] = ["indices", "kpi", "trendband", "global", "main", "bottom", "watchbar"]
LABELS: dict[str, str] = {
    "indices": "指数条",
    "kpi": "KPI 六格",
    "trendband": "趋势带",
    "global": "美股/港股",
    "main": "主体区（分布/情绪/主线/榜单/梯队）",
    "bottom": "底部卡片区（资金/纪要/资讯/国家队）",
    "watchbar": "我的关注",
}


def default_layout() -> dict:
    return {"order": list(SECTIONS), "hidden": []}


def normalize(payload: dict) -> dict:
    """校验并归一化：order 只含合法 id、缺省补齐到末尾；hidden ⊆ 合法 id。

    order 允许只给子集（前端编辑只调顺序时缺省其余自动追加）。
    """
    if not isinstance(payload, dict):
        raise ValueError("布局必须是对象 {order:[], hidden:[]}")
    raw_order = payload.get("order")
    if raw_order is None:
        raw_order = list(SECTIONS)
    if not isinstance(raw_order, list) or any(not isinstance(i, str) for i in raw_order):
        raise ValueError("order 必须是字符串 id 数组")
    unknown = [i for i in raw_order if i not in SECTIONS]
    if unknown:
        raise ValueError(f"未知区块 id：{','.join(unknown)}")
    order: list[str] = []
    for i in raw_order:
        if i not in order:
            order.append(i)
    for i in SECTIONS:
        if i not in order:
            order.append(i)
    raw_hidden = payload.get("hidden") or []
    if not isinstance(raw_hidden, list) or any(not isinstance(i, str) for i in raw_hidden):
        raise ValueError("hidden 必须是字符串 id 数组")
    unknown_h = [i for i in raw_hidden if i not in SECTIONS]
    if unknown_h:
        raise ValueError(f"未知隐藏区块 id：{','.join(unknown_h)}")
    hidden = [i for i in SECTIONS if i in set(raw_hidden)]
    return {"order": order, "hidden": hidden}


def load(path: Path) -> dict:
    if not path.exists():
        return default_layout()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_layout()  # 损坏档回默认，不挡看板
    try:
        return normalize(doc)
    except ValueError:
        return default_layout()


def save(path: Path, payload: dict) -> dict:
    doc = normalize(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return doc
