"""看板 AI 对话框后端（意图识别 / 上下文组装 / 端点）。"""

from .context import (
    TOPIC_KEYWORDS,
    ContextResult,
    Intent,
    build_context,
    parse_intent,
)

__all__ = ["ContextResult", "Intent", "TOPIC_KEYWORDS", "build_context", "parse_intent"]
