# -*- coding: utf-8 -*-
"""抓取+摘要编排（refresh 与 CLI 入口共用）。"""
from __future__ import annotations

from .fetcher import run_fetch
from .digester import run_digest as _run_digest


def run_fetch_pipeline() -> dict:
    """抓取一层的薄封装：返回刷新结果摘要（不含 data 全量，避免无谓复制）。"""
    r = run_fetch()
    return {"ok": True,
            "failed_sources": r["failed_sources"],
            "dup_removed": r["dup_removed"],
            "generated_at": r["data"]["generated_at"]}


def run_digest(cfg: dict) -> dict:
    return _run_digest(cfg)
