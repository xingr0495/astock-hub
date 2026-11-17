"""网页端凭证存储：数据源 API Key + 大模型接口配置。

设计：
- 落盘到项目内 `data/credentials.json`（data/ 已被 .gitignore，不进版本库）。
- 结构：
  {
    "sources": { "<source_id>": {"api_key": "...", "base_url": "...", "extra": {...}} },
    "llm": { "profiles": [ {"id","base_url","model","api_key"} ], "default": "<id>" }
  }
- 明文存储于本机受控目录（与 config.toml 同性质）；对外查询一律脱敏（只回 has_*）。
  如后续需更强保护，可在此模块内替换为加密读写，不影响调用方。

**按用户隔离（2026-11-02 多账号系统任务）**：所有读写函数接受
`user: dict | None = None` 关键字参数——
- `user is None`、管理员、服务令牌身份 → 全局 `data/credentials.json`
  （系统/机主的密钥，cron/推送/TA 等系统消费方不传 user 即走这里）；
- 普通用户 → `data/users/<username>/credentials.json`（各自自己的密钥，
  互不可见互不覆盖）。访客在「数据源」「大模型」设置页填的钥匙只进自己的文件。

并发：Web 进程内用一把锁保护读写，避免并发请求写坏文件。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import PROJECT_ROOT

_CRED_PATH = PROJECT_ROOT / "data" / "credentials.json"
_USERS_ROOT = PROJECT_ROOT / "data" / "users"
_LOCK = threading.Lock()

_EMPTY: dict = {"sources": {}, "llm": {"profiles": [], "default": None}}


class CredentialError(Exception):
    pass


def _cred_file(user: dict | None = None) -> Path:
    """按用户选存储文件：管理员/服务/None → 全局；普通用户 → 自己的目录。"""
    if not user or user.get("is_admin"):
        return _CRED_PATH
    name = str(user.get("username") or "").strip()
    if not name:
        return _CRED_PATH
    return _USERS_ROOT / name / "credentials.json"


def _load(path: Path | None = None) -> dict:
    path = path or _CRED_PATH
    if not path.exists():
        return json.loads(json.dumps(_EMPTY))  # 深拷贝
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        raise CredentialError(f"凭证文件读取失败：{e}") from e
    # 补齐缺失的顶层段，容错旧文件
    data.setdefault("sources", {})
    data.setdefault("llm", {})
    data["llm"].setdefault("profiles", [])
    data["llm"].setdefault("default", None)
    return data


def _save(data: dict, path: Path | None = None) -> None:
    path = path or _CRED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------------- 数据源 ----------------

def set_source(source_id: str, *, api_key: str | None = None,
               base_url: str | None = None, extra: dict | None = None,
               user: dict | None = None) -> None:
    """新增/更新一个数据源凭证。api_key 传 None 表示不改原值，'' 表示清除。"""
    with _LOCK:
        path = _cred_file(user)
        data = _load(path)
        entry = data["sources"].setdefault(source_id, {})
        if api_key is not None:
            entry["api_key"] = api_key
        if base_url is not None:
            entry["api_base_url"] = base_url
        if extra:
            entry["extra"] = extra
        _save(data, path)


def remove_source(source_id: str, user: dict | None = None) -> None:
    with _LOCK:
        path = _cred_file(user)
        data = _load(path)
        data["sources"].pop(source_id, None)
        _save(data, path)


def get_source(source_id: str, user: dict | None = None) -> dict | None:
    """返回单个源的明文配置（供后端取数用，不直接发给前端）。"""
    with _LOCK:
        return _load(_cred_file(user))["sources"].get(source_id)


# ---------------- 大模型 ----------------

def upsert_llm_profile(*, profile_id: str, base_url: str, model: str,
                       api_key: str | None,
                       max_output_tokens: int | None = None,
                       context_window: int | None = None,
                       kind: str = "api",
                       user: dict | None = None) -> None:
    """新增/更新一套大模型接口。api_key=None 表示保留原值。

    kind: "api"（OpenAI 兼容网关，需 base_url/model/key）或
    "claude-cli"（本机 claude 命令行订阅通道，url/key 允许为空）。
    """
    if kind not in ("api", "claude-cli"):
        raise ValueError(f"不支持的 LLM 通道类型：{kind}")
    with _LOCK:
        path = _cred_file(user)
        data = _load(path)
        profiles = data["llm"]["profiles"]
        for p in profiles:
            if p["id"] == profile_id:
                p["base_url"] = base_url
                p["model"] = model
                p["kind"] = kind
                if api_key is not None:
                    p["api_key"] = api_key
                if max_output_tokens is not None:
                    p["max_output_tokens"] = max_output_tokens
                if context_window is not None:
                    p["context_window"] = context_window
                break
        else:
            profiles.append({
                "id": profile_id, "base_url": base_url,
                "model": model,
                "kind": kind,
                "api_key": api_key or "",
                "max_output_tokens": max_output_tokens or 8192,
                "context_window": context_window or 64000,
            })
        if not data["llm"]["default"]:
            data["llm"]["default"] = profile_id
        _save(data, path)


def set_default_llm_profile(profile_id: str, user: dict | None = None) -> None:
    """把已存在的某套大模型接口设为默认（不存在报错，不动文件）。

    所有 LLM 模块每次调用都现读默认档，切换无需重启面板。
    """
    with _LOCK:
        path = _cred_file(user)
        data = _load(path)
        if not any(p["id"] == profile_id for p in data["llm"]["profiles"]):
            raise CredentialError(f"不存在的大模型配置：{profile_id}")
        data["llm"]["default"] = profile_id
        _save(data, path)


def remove_llm_profile(profile_id: str, user: dict | None = None) -> None:
    with _LOCK:
        path = _cred_file(user)
        data = _load(path)
        data["llm"]["profiles"] = [
            p for p in data["llm"]["profiles"] if p["id"] != profile_id]
        if data["llm"]["default"] == profile_id:
            data["llm"]["default"] = (
                data["llm"]["profiles"][0]["id"]
                if data["llm"]["profiles"] else None)
        _save(data, path)


def get_llm_profile(profile_id: str | None = None,
                    *, user: dict | None = None) -> dict | None:
    """返回一套大模型明文配置（后端调 LLM 用）。None 取默认。"""
    with _LOCK:
        llm = _load(_cred_file(user))["llm"]
        pid = profile_id or llm["default"]
        for p in llm["profiles"]:
            if p["id"] == pid:
                p.setdefault("kind", "api")  # 旧档案无 kind 一律按 api
                return p
        return None


# ---------------- 脱敏视图（发给前端） ----------------

def _mask(v: str | None) -> dict:
    has = bool(v)
    return {"configured": has, "hint": (str(v)[:4] + "…") if v else None}


def public_view(user: dict | None = None) -> dict:
    """脱敏后的全部凭证状态：只告诉前端"是否已配置"，不回读明文 key。"""
    with _LOCK:
        data = _load(_cred_file(user))
    sources = {
        sid: {
            "has_api_key": bool(e.get("api_key")),
            "api_base_url": e.get("api_base_url"),
            "key_preview": _mask(e.get("api_key"))["hint"],
            "extra": e.get("extra", {}),
        }
        for sid, e in data["sources"].items()
    }
    profiles = [
        {
            "id": p["id"],
            "base_url": p["base_url"],
            "model": p["model"],
            "kind": p.get("kind", "api"),
            "has_api_key": bool(p.get("api_key")),
            "key_preview": _mask(p.get("api_key"))["hint"],
            "max_output_tokens": p.get("max_output_tokens", 8192),
            "context_window": p.get("context_window", 64000),
        }
        for p in data["llm"]["profiles"]
    ]
    return {
        "sources": sources,
        "llm": {"profiles": profiles, "default": data["llm"]["default"]},
    }
