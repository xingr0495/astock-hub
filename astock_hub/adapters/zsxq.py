"""zsxq（知识星球汇总）适配器：报告文件扫描 + 关键词切片。

报告目录由用户在 config.toml [zsxq].output_dir 自行配置；仓库不附带纪要内容。
- 旧：报告_调研鹅纪要_2026-09-03.md
- 新：9月17日调研鹅纪要汇总_20260917.md（周末合并版如 9月12-13日..._20260913.md）
以文件名末尾的日期后缀为准；正则不匹配时回退文件 mtime 并标注 date_source。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import errors, models
from .base import Adapter, timed

_REPORT_RES = [
    re.compile(r"^报告_调研鹅纪要_(\d{4})-(\d{2})-(\d{2})\.md$"),
    # 新命名已去博主名（调研纪要汇总），兼容旧命名（调研鹅纪要汇总）
    re.compile(r"^.*调研(?:鹅)?纪要汇总_(\d{4})(\d{2})(\d{2})\.md$"),
]

_TOPICS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_topics\.json$")

_CONTEXT_CHARS = 200  # 关键词命中时前后保留的上下文长度


def _extract_contexts(text: str, keyword: str, max_hits: int) -> list[str]:
    """提取关键词命中的上下文切片：扩展到整行边界，并合并重叠窗口。"""
    windows: list[list[int]] = []  # [start, end)
    start = 0
    while len(windows) < max_hits * 2:
        idx = text.find(keyword, start)
        if idx == -1:
            break
        # 向前后扩展到换行符（最多 _CONTEXT_CHARS），让切片是完整行
        s = max(0, idx - _CONTEXT_CHARS)
        nl = text.rfind("\n", s, idx)
        if nl != -1 and nl >= s:
            s = nl + 1
        e = min(len(text), idx + len(keyword) + _CONTEXT_CHARS)
        nl = text.find("\n", idx + len(keyword), e)
        if nl != -1:
            e = nl
        # 与上一个窗口重叠则合并
        if windows and s <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], e)
        else:
            windows.append([s, e])
        start = idx + len(keyword)
    return [text[s:e].strip() for s, e in windows[:max_hits]]


def _parse_report_name(name: str) -> date | None:
    for rx in _REPORT_RES:
        m = rx.match(name)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


class ZsxqAdapter(Adapter):
    name = "zsxq"
    source = "zsxq-files"
    freshness_kind = "daily"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from ..config import PROJECT_ROOT, resolve_path
        self.output_dir = (resolve_path(cfg.get('output_dir'))
                           or PROJECT_ROOT / 'data' / 'zsxq_output')

    def _dir_missing(self) -> models.DimensionResult:
        return self.fail(
            errors.FILE_MISSING, f"找不到 zsxq 输出目录：{self.output_dir}",
            "检查 config.toml 的 [zsxq].output_dir（报告 md 需存在于该目录）",
            endpoint=str(self.output_dir))

    def _list_reports(self) -> list[dict]:
        """所有日报：[{date, path, date_source}]，按日期倒序。"""
        reports = []
        if not self.output_dir.exists():
            return reports
        for p in self.output_dir.glob("*.md"):
            d = _parse_report_name(p.name)
            if d:
                reports.append({"date": d.isoformat(), "path": str(p),
                                "date_source": "filename"})
        if not reports:
            # 回退：任何 md 按 mtime（可能是命名再次漂移）
            for p in self.output_dir.glob("*.md"):
                d = datetime.fromtimestamp(p.stat().st_mtime).date()
                reports.append({"date": d.isoformat(), "path": str(p),
                                "date_source": "mtime"})
        reports.sort(key=lambda r: r["date"], reverse=True)
        return reports

    def health(self) -> models.DimensionResult:
        if not self.output_dir.exists():
            return self._dir_missing()
        reports = self._list_reports()
        if not reports:
            return self.fail(errors.EMPTY_TABLE, "输出目录里没有报告文件",
                             endpoint=str(self.output_dir))
        latest = reports[0]
        return self.ok({"latest_report": latest["path"],
                        "date_source": latest["date_source"],
                        "report_count": len(reports)},
                       as_of=latest["date"], endpoint=str(self.output_dir))

    def latest(self, excerpt_chars: int = 2000) -> models.DimensionResult:
        """最新一份纪要：元信息 + 开头摘录（全文从 path 读，避免撑爆上下文）。"""
        if not self.output_dir.exists():
            return self._dir_missing()
        reports = self._list_reports()
        if not reports:
            return self.not_found("还没有任何纪要报告",
                                  "请在 [zsxq].output_dir 配置自己的纪要报告目录",
                                  endpoint=str(self.output_dir))
        latest = reports[0]
        try:
            with timed() as t:
                text = Path(latest["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return self.fail(errors.PARSE_ERROR, f"报告读取失败：{e}",
                             endpoint=latest["path"])
        return self.ok({**latest, "excerpt": text[:excerpt_chars],
                        "total_chars": len(text)},
                       as_of=latest["date"], endpoint=latest["path"],
                       latency_ms=t["ms"])

    def search(self, keyword: str, days: int = 7, max_hits: int = 10) -> models.DimensionResult:
        """在近 N 天的纪要里搜关键词（股票名/代码），返回命中段落的上下文切片。"""
        if not self.output_dir.exists():
            return self._dir_missing()
        cutoff = date.today() - timedelta(days=days)
        reports = [r for r in self._list_reports()
                   if r["date"] >= cutoff.isoformat()]
        hits = []
        scanned = 0
        for r in reports:
            try:
                text = Path(r["path"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            remaining = max_hits - len(hits)
            if remaining <= 0:
                break
            for context in _extract_contexts(text, keyword, remaining):
                hits.append({
                    "report_date": r["date"],
                    "report_path": r["path"],
                    "context": context,
                })
        if not hits:
            as_of = reports[0]["date"] if reports else None
            return self.not_found(
                f"近 {days} 天的纪要里没有提到「{keyword}」（扫描 {scanned} 份）",
                "不代表没有相关信息——纪要只覆盖付费星球的讨论；也可换个关键词（别名/简称）再试",
                as_of=as_of, endpoint=str(self.output_dir))
        return self.ok({"keyword": keyword, "hits": hits,
                        "reports_scanned": scanned},
                       as_of=reports[0]["date"] if reports else None,
                       endpoint=str(self.output_dir))
