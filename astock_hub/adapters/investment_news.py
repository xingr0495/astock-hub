# -*- coding: utf-8 -*-
"""投资资讯适配器：读 hub 内置管道产物 data/investment_news/news.json。

2026-10-24 全量收编后：产物由 astock_hub/news_pipe 生成（原 investment-news
项目的 fetch/digest 管道），本适配器只读不写。

news.json 结构（与旧 data.js 的 JSON 本体完全一致）：
  {generated_at, recent_days, industries: [{key, name, total, points:[{t,refs}],
     items: [{title,url,time,ts,summary,source, zh?, zh_summary?}]}], stats}

兼容兜底：新档缺失时读旧版 data.js（固定常量，不再读
config——[investment_news] 配置段已删），health note 标注"旧档，建议跑迁移"。
兼容期至云端迁移完成后删除。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import errors, models
from ..news_pipe import store
from .base import Adapter, timed

# 旧外部项目产物（兜底读取；迁移脚本 scripts/migrate_investment_news.py）
LEGACY_DATA_JS = (Path(__file__).resolve().parents[2]
                  / "investment-news" / "data.js")  # 仓库兄弟目录（旧版外部项目）


class InvestmentNewsAdapter(Adapter):
    name = "investment_news"
    source = "investment-news"
    freshness_kind = "daily"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        # store 单点定义路径；测试 monkeypatch store.NEWS_JSON 即生效
        self._news_json = store.NEWS_JSON

    # ---------------- 数据定位与解析 ----------------

    def _data_path(self) -> Path | None:
        if self._news_json.exists():
            return self._news_json
        if LEGACY_DATA_JS.exists():
            return LEGACY_DATA_JS
        return None

    def _is_legacy(self, path: Path) -> bool:
        return path == LEGACY_DATA_JS

    def _load_raw(self, path: Path) -> dict:
        if self._is_legacy(path):
            # 旧 data.js 开头有注释行（可能带 BOM）、末尾可能有追加内容；
            # 用 raw_decode 从第一个 "{" 解析出一个完整 JSON 对象，忽略前后杂质
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            start = text.find("{")
            if start == -1:
                raise ValueError("data.js 里找不到 JSON 对象")
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj
        return json.loads(path.read_text(encoding="utf-8"))

    def _file_missing(self, why: str) -> models.DimensionResult:
        return self.fail(
            errors.FILE_MISSING, why,
            "先在面板刷新产业资讯，或运行 astock-hub refresh --source news"
            "（--force 强制重跑）",
            endpoint=str(self._news_json))

    def _load(self) -> tuple[dict, Path] | None:
        """定位并解析数据；失败返回 None（各方法自行给差异化的错误）。"""
        path = self._data_path()
        if path is None:
            return None
        return self._load_raw(path), path

    def _as_of(self, data: dict) -> str:
        raw = data.get("generated_at") or ""
        # "2026-09-17 08:54" → "2026-09-17"
        return raw[:10] if raw else None

    def _note(self, path: Path | None) -> str:
        note = "全球产业链资讯（12 赛道）——AI 今日要点层缺失时，要点为空、只显示新闻列表"
        if path is not None and self._is_legacy(path):
            note += "（旧档，建议跑迁移 scripts/migrate_investment_news.py）"
        return note

    # ---------------- 三方法 ----------------

    def health(self) -> models.DimensionResult:
        path = self._data_path()
        if path is None:
            return self._file_missing("找不到投资资讯数据文件（news.json/旧档均无）")
        try:
            with timed() as t:
                data = self._load_raw(path)
        except (ValueError, OSError) as e:
            return self.fail(errors.PARSE_ERROR, f"news.json 解析失败：{e}",
                             endpoint=str(path))
        industries = data.get("industries") or []
        with_points = sum(1 for ind in industries if ind.get("points"))
        return self.ok(
            {"generated_at": data.get("generated_at"),
             "industries": len(industries),
             "industries_with_points": with_points,
             "total_sources": (data.get("stats") or {}).get("total_sources")},
            as_of=self._as_of(data), endpoint=str(path), latency_ms=t["ms"],
            note=self._note(path))

    def latest(self, industry: str | None = None,
               max_items: int = 5) -> models.DimensionResult:
        """各赛道最新动向：AI 今日要点 + 顶部新闻（含中文标题）。"""
        loaded = self._load()
        if loaded is None:
            return self._file_missing("找不到投资资讯数据文件（news.json/旧档均无）")
        data, path = loaded
        with timed() as t:
            industries = data.get("industries") or []
            if industry:
                industries = [i for i in industries
                              if i.get("key") == industry or industry in i.get("name", "")]
                if not industries:
                    return self.not_found(
                        f"没有叫「{industry}」的赛道，可选："
                        + ", ".join(f"{i['key']}({i['name']})" for i in data.get("industries", [])),
                        endpoint=str(path))
            payload = []
            for ind in industries:
                items = ind.get("items") or []
                payload.append({
                    "key": ind.get("key"),
                    "name": ind.get("name"),
                    "total": ind.get("total"),
                    "points": ind.get("points") or [],
                    "top_items": [
                        {"title": it.get("title"), "zh": it.get("zh"),
                         "zh_summary": it.get("zh_summary"),
                         "source": it.get("source"), "time": it.get("time"),
                         "summary": it.get("summary"),
                         "url": it.get("url")}
                        for it in items[:max_items]
                    ],
                })
        return self.ok({"generated_at": data.get("generated_at"), "industries": payload},
                       as_of=self._as_of(data), endpoint=str(path),
                       latency_ms=t["ms"], note=self._note(path))

    def search(self, keyword: str, days: int | None = None,
               max_hits: int = 8) -> models.DimensionResult:
        """在所有赛道新闻里搜关键词（英文标题/中文标题/摘要/来源）。"""
        loaded = self._load()
        if loaded is None:
            return self._file_missing("找不到投资资讯数据文件（news.json/旧档均无）")
        data, path = loaded
        hits = []
        for ind in data.get("industries") or []:
            for it in ind.get("items") or []:
                hay = " ".join(str(x) for x in
                               (it.get("title"), it.get("zh"), it.get("zh_summary"),
                                it.get("summary"), it.get("source")) if x)
                if keyword.lower() in hay.lower():
                    hits.append({
                        "industry": f"{ind.get('key')}({ind.get('name')})",
                        "title": it.get("title"),
                        "zh": it.get("zh"),
                        "zh_summary": it.get("zh_summary"),
                        "source": it.get("source"),
                        "time": it.get("time"),
                        "url": it.get("url"),
                    })
                    if len(hits) >= max_hits:
                        break
            if len(hits) >= max_hits:
                break
        if not hits:
            return self.not_found(f"投资资讯近期新闻里没有命中「{keyword}」",
                                  "只覆盖 12 大赛道的全球产业源，可换英文关键词试试",
                                  as_of=self._as_of(data),
                                  endpoint=str(path))
        return self.ok({"keyword": keyword, "hits": hits,
                        "generated_at": data.get("generated_at")},
                       as_of=self._as_of(data), endpoint=str(path))
