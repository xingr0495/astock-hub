"""推送调度器：管理多个目标，异步发送"""

from __future__ import annotations

import logging
import threading
from typing import List

from .sender import create_sender

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """通知调度器：管理多个 Webhook 目标，异步分发"""

    def __init__(self, targets: list[dict] = None):
        """
        targets: [{"url": "https://...", "type": "feishu|wechat", "filter": ["review","analysis"]}, ...]
        filter 不填或空 = 全部推
        """
        self.targets = targets or []

    def dispatch(self, content: str, title: str = "astock-hub",
                 content_type: str = "all") -> list[dict]:
        """同步发送到所有匹配目标，返回结果列表"""
        results = []
        for target in self.targets:
            url = target.get("url", "")
            target_filter = target.get("filter", [])
            if target_filter and content_type not in target_filter:
                continue
            sender = create_sender(url, target.get("type"))
            result = sender.send(content, title)
            results.append({"url": url, **result})
            if not result.get("ok"):
                logger.warning("推送失败 %s: %s", url[:50], result)
        return results

    def dispatch_async(self, content: str, title: str = "astock-hub",
                       content_type: str = "all"):
        """异步发送（不阻塞主流程）"""
        threading.Thread(
            target=self.dispatch,
            args=(content, title, content_type),
            daemon=True
        ).start()


def get_dispatcher(cfg: dict = None) -> NotificationDispatcher:
    """从配置读取推送目标"""
    from .config import load_config as load_notify_config

    notify_cfg = load_notify_config()
    targets = []
    for t in notify_cfg.get("targets", []):
        url = t.get("url", "")
        if url:
            targets.append({
                "url": url,
                "type": t.get("type", ""),
                "filter": t.get("content_filter", []),
            })
    return NotificationDispatcher(targets)
