"""告警规则 CRUD（JSON 文件存储）"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import load_config

ALERT_RULES_FILE = Path(__file__).resolve().parents[2] / "data" / "alert_rules.json"


def _rules_path() -> Path:
    cfg = load_config()
    data_dir = Path(cfg.get("alert", {}).get("data_dir", "data"))
    return data_dir / "alert_rules.json"


def load_rules() -> list[dict]:
    p = _rules_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def save_rules(rules: list[dict]):
    p = _rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def add_rule(rule: dict) -> dict:
    rules = load_rules()
    max_id = max((r.get("id", 0) for r in rules), default=0)
    rule["id"] = max_id + 1
    rule["enabled"] = True
    rules.append(rule)
    save_rules(rules)
    return rule


def update_rule(rule_id: int, updates: dict) -> dict | None:
    rules = load_rules()
    for r in rules:
        if r.get("id") == rule_id:
            r.update(updates)
            save_rules(rules)
            return r
    return None


def remove_rule(rule_id: int) -> bool:
    rules = load_rules()
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) < len(rules):
        save_rules(new_rules)
        return True
    return False


def get_rule(rule_id: int) -> dict | None:
    return next((r for r in load_rules() if r.get("id") == rule_id), None)
