"""Webhook 发送器：飞书/企业微信"""

from __future__ import annotations

import httpx


class FeishuWebhook:
    """飞书机器人 Webhook（卡片消息）"""

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, content: str, title: str = None) -> dict:
        """发送飞书卡片消息"""
        try:
            payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"content": title or "astock-hub 通知", "tag": "plain_text"}
                    },
                    "elements": [{"tag": "markdown", "content": content}]
                }
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.url, json=payload)
                data = resp.json()
                return {"ok": resp.status_code == 200 and data.get("code") == 0,
                        "detail": data}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class WechatWebhook:
    """企业微信机器人 Webhook（markdown）"""

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, content: str, title: str = None) -> dict:
        """发送企业微信 markdown 消息（单条上限 4096 字节）"""
        try:
            # markdown 内容拼接标题
            md_content = f"## {title}\n\n{content}" if title else content

            # 单条上限 4096 字节
            max_bytes = 4096
            chunks = [md_content[i:i+max_bytes] for i in range(0, len(md_content.encode("utf-8")), max_bytes)] or [md_content]

            last_result = {}
            for chunk in chunks:
                payload = {
                    "msgtype": "markdown",
                    "markdown": {"content": chunk}
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(self.url, json=payload)
                    last_result = resp.json()
            return {"ok": last_result.get("errcode") == 0, "detail": last_result}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def create_sender(webhook_url: str, channel_type: str = None):
    """工厂函数：根据 URL 或指定类型创建对应的 sender"""
    if channel_type == "feishu" or "feishu" in webhook_url.lower() or "lark" in webhook_url.lower():
        return FeishuWebhook(webhook_url)
    elif channel_type == "wechat" or "qyapi" in webhook_url.lower():
        return WechatWebhook(webhook_url)
    else:
        # 默认企微
        return WechatWebhook(webhook_url)
