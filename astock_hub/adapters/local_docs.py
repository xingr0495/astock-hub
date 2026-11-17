# -*- coding: utf-8 -*-
"""本地资料适配器：扫描本地资料 text/*.txt，接口与 ZsxqAdapter 对齐。

按用户隔离：构造时传 user/is_admin，只扫该用户的目录（data/users/<u>/local_docs）；
不传（CLI/旧调用）走旧全局 data/local_docs。

hits 形状与 zsxq 一致（report_date/report_path/context），pool 里按
source='local' 标注，评分权重同纪要（2.0）；时间窗默认 30 天（纪要 7 天——
上传少而精，7 天太短；超期不参与选股但面板仍可看）。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .. import models
from ..local_docs import _load_index
from ..local_docs import root_of as _root
from .base import Adapter
from .zsxq import _extract_contexts


class LocalDocsAdapter(Adapter):
    name = "local_docs"
    source = "local-docs-files"
    freshness_kind = "daily"

    def __init__(self, cfg: dict, user: str | None = None,
                 is_admin: bool = False):
        super().__init__(cfg)
        self.user = user
        self.is_admin = is_admin

    def search(self, keyword: str, days: int = 30, max_hits: int = 10
               ) -> models.DimensionResult:
        """在近 N 天（默认 30）上传的本地资料全文里搜关键词（只扫当前用户目录）。"""
        # 不传用户走 1 参调用：既有测试把 _root 打成单参桩，保持兼容
        root = (_root(self.cfg, self.user, self.is_admin) if self.user
                else _root(self.cfg))
        cutoff = date.today() - timedelta(days=days)
        docs = [d for d in _load_index(root)
                if (d.get("upload_date") or "") >= cutoff.isoformat()]
        # index 是插入序（最旧在前）：倒序保证 as_of 与 hits 都是最新优先
        docs.sort(key=lambda d: d.get("upload_date") or "", reverse=True)
        hits, scanned = [], 0
        for d in docs:
            text_path = root / "text" / f"{d['doc_id']}.txt"
            if not text_path.exists():
                continue  # extract_failed 的没有文本，天然跳过
            try:
                text = text_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            remaining = max_hits - len(hits)
            if remaining <= 0:
                break
            for context in _extract_contexts(text, keyword, remaining):
                hits.append({
                    "report_date": d.get("upload_date"),
                    "report_path": str(text_path),
                    "doc_name": d.get("name") or d.get("doc_id"),
                    "context": context,
                })
        if not hits:
            return self.not_found(
                f"近 {days} 天的本地资料里没有提到「{keyword}」（扫描 {scanned} 份）",
                "本地资料只覆盖用户手动上传的文档；可在产业资讯页「本地资料」上传",
                endpoint=str(root))
        return self.ok({"keyword": keyword, "hits": hits,
                        "reports_scanned": scanned},
                       as_of=docs[0].get("upload_date") if docs else None,
                       endpoint=str(root))
