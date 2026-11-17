"""配置加载：config.toml + 环境变量覆盖（环境变量优先级最高）。"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 环境变量 → (配置段, 配置键)
_ENV_MAP = {
    "ASTOCK_HUB_NT_DSN": ("national_team", "dsn"),
    "ASTOCK_HUB_NT_ETF_JSON": ("national_team", "etf_json_path"),
    "ASTOCK_HUB_ZSXQ_OUTPUT": ("zsxq", "output_dir"),
    "ASTOCK_HUB_HITHINK_CLI": ("hithink", "cli"),
    "ASTOCK_HUB_HITHINK_CACHE": ("hithink_data", "cache_dir"),
    "ASTOCK_HUB_HITHINK_STORE": ("hithink_data", "store_dir"),
    "ASTOCK_HUB_IFIND_SKILL_DIR": ("ifind", "skill_dir"),
    "ASTOCK_HUB_TUSHARE_TOKEN": ("tushare", "api_key"),
    "ASTOCK_HUB_TUSHARE_URL": ("tushare", "base_url"),
    "ASTOCK_HUB_DATA_PRIMARY": ("data_source", "primary"),
}

# 含密码等敏感内容的键，config --show 时脱敏
_SENSITIVE_KEYS = {"dsn"}


class ConfigError(Exception):
    pass


def load_config(path: str | None = None) -> dict:
    """加载配置。默认读项目根目录 config.toml，缺失时退回 config.example.toml。"""
    candidates = [path] if path else [PROJECT_ROOT / "config.toml", PROJECT_ROOT / "config.example.toml"]
    cfg: dict = {}
    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            with open(p, "rb") as f:
                cfg = tomllib.load(f)
            cfg["_config_path"] = str(p)
            break
    if not cfg:
        raise ConfigError(f"找不到配置文件（尝试过：{[str(c) for c in candidates]}）")
    for env_key, (section, key) in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value:
            cfg.setdefault(section, {})[key] = value
    return cfg


def resolve_path(value: str | os.PathLike[str] | None) -> Path | None:
    """把配置里的路径解析成绝对路径。

    - None / 空字符串 → None（外部可选数据缺省时调用方据此跳过）
    - 绝对路径 → 原样返回
    - 相对路径 → 基于项目根 PROJECT_ROOT 解析（开源用户 clone 后零配置）
    """
    if value is None:
        return None
    p = Path(value).expanduser()
    if str(value).strip() == "":
        return None
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def mask_sensitive(cfg: dict) -> dict:
    """返回脱敏后的配置副本（密码部分替换为 ***），用于 config --show。"""
    masked = {}
    for section, values in cfg.items():
        if not isinstance(values, dict):
            masked[section] = values
            continue
        masked[section] = {}
        for key, value in values.items():
            if key in _SENSITIVE_KEYS and isinstance(value, str) and "://" in value:
                # postgresql://user:password@host:port/db → postgresql://user:***@host:port/db
                scheme, rest = value.split("://", 1)
                if "@" in rest and ":" in rest.split("@", 1)[0]:
                    user, _pw = rest.split("@", 1)[0].split(":", 1)
                    rest = f"{user}:***@{rest.split('@', 1)[1]}"
                masked[section][key] = f"{scheme}://{rest}"
            else:
                masked[section][key] = value
    return masked
