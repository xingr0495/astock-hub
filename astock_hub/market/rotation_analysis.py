"""板块轮动 AI 分析（B1-3）：五信号预计算 + 7 章节流式研判。

五信号（对齐 TSP concept_rotation_analyzer）：
- persistent_leaders：窗口内平均排名最靠前的持续主线
- rising：排名上升最快（新晋）
- fading：排名下滑最快（退潮）
- institutional：排名波动最小（机构主导、走趋势）
- hot_money：排名波动最大（游资主导、追题材）

只输出分析与观察清单，不输出买卖指令。
"""
from __future__ import annotations

import statistics
from typing import Any

from . import service as market_service

_WINDOWS = (3, 5, 10)


def _series(columns: dict[str, list]) -> dict[str, dict]:
    """columns: {date: [[name, avg_pct], ...]} → {name: {ranks, pcts, dates}}。"""
    out: dict[str, dict] = {}
    for d, rows in columns.items():
        for i, (name, pct) in enumerate(rows):
            s = out.setdefault(name, {"ranks": [], "pcts": [], "dates": []})
            s["ranks"].append(i + 1)
            s["pcts"].append(pct)
            s["dates"].append(d)
    return out


def rotation_signals(cfg: dict, days: int = 12, kind: str = "concept") -> dict:
    snap = market_service.rps_snapshot(cfg, days=days, refresh=False)
    kinds = (snap or {}).get("kinds") or {}
    k = kinds.get(kind) or next(iter(kinds.values()), {})
    columns = k.get("columns") or {}
    dates = sorted(columns.keys())[-days:]
    columns = {d: columns[d] for d in dates}
    series = _series(columns)

    stats = []
    for name, s in series.items():
        ranks = s["ranks"]
        pcts = s["pcts"]
        if len(ranks) < 3:
            continue
        last_rank = ranks[-1]
        last_pct = pcts[-1]
        avg_rank = statistics.mean(ranks)
        rank_std = statistics.pstdev(ranks) if len(ranks) > 1 else 0.0
        recent = statistics.mean(ranks[-3:])
        early = statistics.mean(ranks[:3])
        stats.append({
            "name": name, "last_rank": last_rank, "last_pct": last_pct,
            "avg_rank": round(avg_rank, 2), "rank_std": round(rank_std, 2),
            "delta": round(early - recent, 2),  # >0=排名上升（数值变小）
        })

    def top(items, key, n=5, reverse=False):
        return sorted(items, key=key, reverse=reverse)[:n]

    return {
        "persistent_leaders": top(stats, lambda x: x["avg_rank"]),
        "rising": top(stats, lambda x: x["delta"], reverse=True),
        "fading": top(stats, lambda x: x["delta"]),
        "institutional": top(stats, lambda x: x["rank_std"]),
        "hot_money": top(stats, lambda x: x["rank_std"], reverse=True),
        "dates": dates,
    }


def build_messages(signals: dict) -> list[dict]:
    import json
    system = (
        "你是 A 股板块轮动分析助手。基于给定的五组板块信号写一份结构化研判，"
        "严格按这 7 节输出：1 主线研判 2 新晋方向 3 退潮方向 4 机构 vs 游资 "
        "5 大盘环境 6 观察清单 7 风险提示。"
        "只给事实归纳与观察建议，**禁止给出具体买卖指令、目标价或仓位建议**；"
        "没有数据支撑就说没有，不要编造板块。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "五组板块信号如下（JSON）：\n"
         + json.dumps(signals, ensure_ascii=False, indent=2)},
    ]
