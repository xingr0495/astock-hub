"""适配器基类与公共工具。

每个适配器负责一路数据源；所有方法返回 DimensionResult，异常在适配器内部
转成结构化 error，绝不让一路源的异常泄漏出去拖垮整体。
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from .. import errors, models
from ..freshness import assess


class Adapter:
    name = "base"
    source = "base"
    freshness_kind = "daily"

    def __init__(self, cfg: dict):
        self.cfg = cfg  # 本适配器对应的配置段
        self._full_cfg = None  # 完整配置（需要跨源时由 cli 注入）

    def health(self) -> models.DimensionResult:
        raise NotImplementedError

    # ---- 构造返回的辅助方法 ----

    def ok(self, data, as_of=None, endpoint=None, latency_ms=None, note=None,
           status=None, kind=None, source=None) -> models.DimensionResult:
        kind = kind or self.freshness_kind
        freshness = assess(as_of, kind) if as_of else None
        if status is None:
            status = models.OK
            if freshness and freshness.get("stale"):
                status = models.STALE
        meta = models.SourceMeta(
            as_of=str(as_of) if as_of is not None else None,
            freshness_kind=kind,
            freshness=freshness,
            endpoint_or_path=endpoint,
            latency_ms=latency_ms,
            note=note,
        )
        return models.DimensionResult(status=status, source=source or self.source,
                                      meta=meta, data=data)

    def not_found(self, message: str, hint: str | None = None, as_of=None,
                  endpoint=None, kind=None) -> models.DimensionResult:
        """查到了、但数据里没这个对象——这是正常状态，不是错误。"""
        kind = kind or self.freshness_kind
        meta = models.SourceMeta(
            as_of=str(as_of) if as_of is not None else None,
            freshness_kind=kind,
            freshness=assess(as_of, kind) if as_of else None,
            endpoint_or_path=endpoint,
        )
        return models.DimensionResult(
            status=models.NOT_FOUND, source=self.source, meta=meta,
            data=None,
            error=errors.make_error(errors.NOT_FOUND, message, hint),
        )

    def fail(self, code: str, message: str, hint: str | None = None,
             retryable: bool = False, endpoint=None) -> models.DimensionResult:
        return models.DimensionResult(
            status=models.UNAVAILABLE, source=self.source,
            meta=models.SourceMeta(freshness_kind=self.freshness_kind,
                                   endpoint_or_path=endpoint),
            data=None,
            error=errors.make_error(code, message, hint, retryable),
        )


@contextmanager
def timed():
    """with timed() as t: ... → t['ms'] 为耗时毫秒。"""
    holder = {"ms": None}
    start = time.perf_counter()
    try:
        yield holder
    finally:
        holder["ms"] = int((time.perf_counter() - start) * 1000)
