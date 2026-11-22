"""情绪阶段 × 每日主线历史矩阵（B2-7）。

把连续同阶段交易日合成一个阶段段，再汇总段内每日主线：上榜天数、最高连板、
峰值日龙头。成分映射是当前快照回看历史，MEMBERSHIP_NOTE 必须随结果披露。
"""
from __future__ import annotations

import time

import polars as pl

from ..store.concepts import MEMBERSHIP_NOTE
from . import mainline as ml
from . import service as market_service

_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 300


def build_phase_matrix(history: list[dict], mainline: pl.DataFrame,
                       kind: str = "concept") -> list[dict]:
    rows = sorted(history, key=lambda r: str(r.get("date")))
    segments: list[dict] = []
    for row in rows:
        day = str(row.get("date"))[:10]
        phase = row.get("phase") or "unknown"
        if not segments or segments[-1]["phase"] != phase:
            segments.append({"phase": phase, "start": day, "end": day, "days": 0})
        segments[-1]["end"] = day
        segments[-1]["days"] += 1

    records = []
    if not mainline.is_empty():
        for r in mainline.iter_rows(named=True):
            records.append({**r, "date": str(r["date"])[:10]})

    for seg in segments:
        subset = [r for r in records if seg["start"] <= r["date"] <= seg["end"]]
        by_member: dict[str, list[dict]] = {}
        for r in subset:
            by_member.setdefault(str(r.get("member") or ""), []).append(r)
        items = []
        for name, rs in by_member.items():
            if not name:
                continue
            peak = max(rs, key=lambda r: (int(r.get("max_boards") or 0), r["date"]))
            items.append({
                "name": name,
                "days": len({r["date"] for r in rs}),
                "max_boards": max(int(r.get("max_boards") or 0) for r in rs),
                "leader_symbol": peak.get("leader_symbol"),
                "peak_date": peak["date"],
                "best_rank": min(int(r.get("rank") or 999) for r in rs),
            })
        items.sort(key=lambda x: (-x["days"], -x["max_boards"], x["best_rank"], x["name"]))
        seg["items"] = items[:5]
        seg["mainline_days"] = len({r["date"] for r in subset})
    return list(reversed(segments))  # 最近阶段段在前


def phase_mainline_matrix(cfg: dict, *, days: int = 120,
                          kind: str = "concept") -> dict:
    key = (days, kind)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    snap = market_service.snapshot(cfg, days=days)
    history = snap.get("history") or []
    mainline = ml.load_mainline_history(market_service.store_dir(cfg), kind)
    segments = build_phase_matrix(history, mainline, kind)
    result = {
        "available": bool(segments),
        "kind": kind,
        "as_of": (snap.get("latest") or {}).get("date"),
        "segments": segments,
        "membership_note": MEMBERSHIP_NOTE,
    }
    _CACHE[key] = (time.monotonic(), result)
    return result
