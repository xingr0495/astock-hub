# -*- coding: utf-8 -*-
"""news_pipe 产物与源清单的路径/读写（单点定义，原子落盘）。"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import PROJECT_ROOT

NEWS_DIR = PROJECT_ROOT / "data" / "investment_news"
NEWS_JSON = NEWS_DIR / "news.json"
ZH_CACHE_JSON = NEWS_DIR / "zh_cache.json"
DEFAULT_SOURCES = Path(__file__).with_name("default_sources.json")

ZH_CACHE_MAX = 8000


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_sources() -> dict:
    return json.loads(DEFAULT_SOURCES.read_text(encoding="utf-8"))


def load_news() -> dict | None:
    return _read_json(NEWS_JSON)


def save_news(data: dict) -> None:
    _atomic_write_json(NEWS_JSON, data)


def load_zh_cache() -> dict:
    return _read_json(ZH_CACHE_JSON) or {}


def save_zh_cache(cache: dict, cap: int = ZH_CACHE_MAX) -> None:
    if len(cache) > cap:
        cache = dict(list(cache.items())[-cap:])
    _atomic_write_json(ZH_CACHE_JSON, cache)
