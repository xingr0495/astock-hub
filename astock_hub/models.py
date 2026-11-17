"""统一返回结构（Envelope / DimensionResult / SourceMeta）。

所有 CLI 命令与 MCP 工具返回同一个 Envelope 结构；
每个维度独立成败，一路数据源挂掉不影响其他维度返回。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "1.0"

# 维度状态
OK = "ok"
STALE = "stale"
PARTIAL = "partial"
NOT_FOUND = "not_found"
UNAVAILABLE = "unavailable"
SKIPPED = "skipped"
NOT_APPLICABLE = "not_applicable"

ALL_STATUSES = (OK, STALE, PARTIAL, NOT_FOUND, UNAVAILABLE, SKIPPED, NOT_APPLICABLE)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class SourceMeta:
    """数据出处与新鲜度。as_of 永远取数据本身的时间戳，不是抓取时间。"""

    as_of: str | None = None
    fetched_at: str = field(default_factory=now_iso)
    freshness_kind: str = "daily"  # realtime / daily / weekly / quarterly
    freshness: dict | None = None
    endpoint_or_path: str | None = None
    latency_ms: int | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        d = {
            "as_of": self.as_of,
            "fetched_at": self.fetched_at,
            "freshness_kind": self.freshness_kind,
        }
        if self.freshness is not None:
            d["freshness"] = self.freshness
        if self.endpoint_or_path:
            d["endpoint_or_path"] = self.endpoint_or_path
        if self.latency_ms is not None:
            d["latency_ms"] = self.latency_ms
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class DimensionResult:
    """单个维度（或单个子命令）的返回。"""

    status: str
    source: str
    meta: SourceMeta = field(default_factory=SourceMeta)
    data: Any = None
    error: dict | None = None

    def to_dict(self) -> dict:
        d = {
            "status": self.status,
            "source": self.source,
            "meta": self.meta.to_dict(),
            "data": self.data,
            "error": self.error,
        }
        return d


@dataclass
class Envelope:
    """命令级返回。dimensions 为空时表示单数据源命令（结果直接放 data）。"""

    command: str
    query: dict = field(default_factory=dict)
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    data: Any = None
    generated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "command": self.command,
            "generated_at": self.generated_at,
        }
        if self.query:
            d["query"] = self.query
        if self.dimensions:
            d["dimensions"] = {k: v.to_dict() for k, v in self.dimensions.items()}
            d["summary"] = summarize(self.dimensions)
        if self.data is not None:
            d["data"] = self.data
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def summarize(dimensions: dict[str, DimensionResult]) -> dict:
    counts: dict[str, int] = {}
    for dim in dimensions.values():
        counts[dim.status] = counts.get(dim.status, 0) + 1
    return counts
