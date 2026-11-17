"""推送配置 CRUD（JSON 文件存储）"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parents[2] / "data" / "notify_config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            # 旧配置文件没有告警两键（2026-10-24 新增）：读取时补齐默认
            data.setdefault("alert_enabled", False)
            data.setdefault("alert_webhook_url", "")
            data.setdefault("content_filters", {}).setdefault("alert", True)
            return data
        except json.JSONDecodeError:
            pass
    return {
        "enabled": False,
        "targets": [],
        "content_filters": {
            "review": True,
            "analysis": True,
            "alert": True,
            "signal": True,
        },
        "review_enabled": True,
        "review_time": "16:20",
        "review_no_early": "15:00",
        "alert_enabled": False,
        "alert_webhook_url": "",
    }


def save_config(config: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
