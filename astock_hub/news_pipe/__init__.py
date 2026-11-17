# -*- coding: utf-8 -*-
"""全球产业新闻管道（收编自 investment-news，2026-10-24 全量收编）。

- fetcher：106 源 RSS/Atom 抓取（行为口径冻结自 investment-news/scripts/fetch.py）
- digester：今日要点 + 中文翻译/摘要（LLM 走 hub 统一通道 call_llm）
- builder：源清单策展/liveness 实测工具（手动跑，非日常链）
- runner：编排（refresh/CLI 入口用 `from .news_pipe import runner`）

注意：本包 __init__ **刻意不导出 runner**（digester 顶层 import llm_client，
llm_client 又依赖 serenity，serenity 顶层 import 适配器——在包初始化时导入
runner 会绕成循环导入）。调用方一律显式 `from .news_pipe import runner`。
"""

