# astock_hub/auth.py
"""多账号认证与权限（上云前置，规格 docs/superpowers/specs/2026-11-02-multiaccount-auth-design.md）。

存储：data/auth.json（锁 + 原子写，仿 credentials.py）。密码 PBKDF2-SHA256 20 万轮
+ 每用户随机 salt（纯标准库）。会话为服务端随机 token。_TEST_BYPASS 仅供 pytest
conftest 打开（测试旁路），生产代码路径无任何开关。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import PROJECT_ROOT

_AUTH_PATH = PROJECT_ROOT / "data" / "auth.json"
_LOCK = threading.Lock()
_RL_LOCK = threading.Lock()
_TEST_BYPASS = False

_BJT = timezone(timedelta(hours=8))
PERMS = ("view_market", "view_review", "view_news", "pool_screen",
         "watchlist", "ai_chat", "zsxq", "local_docs",
         "data_sources", "llm_settings", "paper_trading", "backtest")
DEFAULT_PERMS = {k: (k == "view_market") for k in PERMS}
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,32}$")
# Windows 保留设备名（casefold 后比对）：con.json / aux.json 这类不是合法文件路径，
# 用户名落盘（自选/本地资料）时会把该用户打成 500
_RESERVED_WINDOWS_NAMES = frozenset(
    ["con", "aux", "nul", "prn"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)

_FAIL_WINDOW_SEC = 600
_FAIL_MAX = 10
_failures: dict[str, list[float]] = {}

_EMPTY = {"registration_keys": [], "users": [], "sessions": {}}


class AuthError(Exception):
    """业务错误：消息直接作为 HTTP error 体返回给前端。"""


def _now_iso() -> str:
    return datetime.now(_BJT).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(_BJT).strftime("%Y-%m-%d")


def _load() -> dict:
    if not _AUTH_PATH.exists():
        return json.loads(json.dumps(_EMPTY))
    try:
        with open(_AUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        raise AuthError(f"认证文件读取失败：{e}") from e
    data.setdefault("registration_keys", [])
    # 旧单密钥格式（registration_key: str）迁入多密钥列表；下次 _save 落新格式
    legacy = data.pop("registration_key", None)
    if (isinstance(legacy, str) and legacy
            and not any((e.get("key") if isinstance(e, dict) else e) == legacy
                        for e in data["registration_keys"])):
        data["registration_keys"].append(
            {"key": legacy, "created_at": _now_iso()})
    data.setdefault("users", [])
    data.setdefault("sessions", {})
    return data


def _save(data: dict) -> None:
    _AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _AUTH_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(_AUTH_PATH)


def _validate_username(username: str) -> None:
    if not isinstance(username, str) or not _USERNAME_RE.match(username):
        raise AuthError("用户名需为 2-32 位字母、数字、下划线或短横线")
    if username.casefold() in _RESERVED_WINDOWS_NAMES:
        raise AuthError("该用户名是系统保留名，请换一个")


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise AuthError("密码至少 8 位")


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000
    ).hex()


def _public(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"],
            "is_admin": bool(u.get("is_admin")), "disabled": bool(u.get("disabled", False)),
            "perms": dict(u.get("perms") or DEFAULT_PERMS),
            "created_at": u.get("created_at")}


def _find(data: dict, user_id: str) -> dict:
    for u in data["users"]:
        if u["id"] == user_id:
            return u
    raise AuthError("账号不存在")


# ---------------- 用户生命周期 ----------------

def need_bootstrap() -> bool:
    with _LOCK:
        return not _load()["users"]


def bootstrap_admin(username: str, password: str) -> dict:
    _validate_username(username)
    _validate_password(password)
    with _LOCK:
        data = _load()
        if data["users"]:
            raise AuthError("系统已初始化")
        salt = secrets.token_hex(16)
        user = {"id": f"u_{secrets.token_hex(4)}", "username": username,
                "pwd_hash": _hash_password(password, salt), "salt": salt,
                "is_admin": True, "disabled": False,
                "perms": dict(DEFAULT_PERMS),
                "ai_usage": {"date": _today(), "count": 0},
                "created_at": _now_iso()}
        data["users"].append(user)
        _save(data)
        return _public(user)


def register_user(username: str, password: str, invite_key: str) -> dict:
    _validate_username(username)
    _validate_password(password)
    with _LOCK:
        data = _load()
        # 多密钥（2026-11-02 用户拍板"注册密钥可以生成多个"）：任一在册密钥
        # 命中即放行；bytes 版 compare_digest（非 ASCII 不抛 TypeError）
        ok = False
        if isinstance(invite_key, str) and invite_key:
            for entry in data.get("registration_keys") or []:
                stored = entry.get("key") if isinstance(entry, dict) else entry
                if isinstance(stored, str) and stored and secrets.compare_digest(
                        stored.encode("utf-8"), invite_key.encode("utf-8")):
                    ok = True
        if not ok:
            raise AuthError("注册密钥错误或注册已关闭")
        # casefold 查重：Windows 文件系统大小写不敏感，alice/Alice 会共享同一份
        # 落盘文件（自选/本地资料互串）→ 必须在注册口拦死大小写变体
        if any(u["username"].casefold() == username.casefold()
               for u in data["users"]):
            raise AuthError("用户名已存在")
        salt = secrets.token_hex(16)
        user = {"id": f"u_{secrets.token_hex(4)}", "username": username,
                "pwd_hash": _hash_password(password, salt), "salt": salt,
                "is_admin": False, "disabled": False,
                "perms": dict(DEFAULT_PERMS),
                "ai_usage": {"date": _today(), "count": 0},
                "created_at": _now_iso()}
        data["users"].append(user)
        _save(data)
        return _public(user)


def authenticate(username: str, password: str) -> dict | None:
    with _LOCK:
        data = _load()
        for u in data["users"]:
            if u["username"] == username and not u.get("disabled"):
                if isinstance(password, str) and secrets.compare_digest(
                        _hash_password(password, u["salt"]), u["pwd_hash"]):
                    return _public(u)
        return None


def list_users() -> list[dict]:
    with _LOCK:
        return [_public(u) for u in _load()["users"]]


def get_user(user_id: str) -> dict | None:
    with _LOCK:
        try:
            return _public(_find(_load(), user_id))
        except AuthError:
            return None


def set_perms(user_id: str, perms: dict) -> dict:
    if not isinstance(perms, dict):
        raise AuthError("perms 必须是对象")
    clean = {k: bool(perms.get(k, False)) for k in PERMS}
    with _LOCK:
        data = _load()
        u = _find(data, user_id)
        u["perms"] = clean
        _save(data)
        return _public(u)


def set_disabled(user_id: str, disabled: bool) -> None:
    with _LOCK:
        data = _load()
        u = _find(data, user_id)
        u["disabled"] = bool(disabled)
        if disabled:
            data["sessions"] = {t: s for t, s in data["sessions"].items()
                                if s.get("user_id") != user_id}
        _save(data)


def reset_password(user_id: str, password: str) -> None:
    _validate_password(password)
    with _LOCK:
        data = _load()
        u = _find(data, user_id)
        salt = secrets.token_hex(16)
        u["salt"] = salt
        u["pwd_hash"] = _hash_password(password, salt)
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if s.get("user_id") != user_id}
        _save(data)


def delete_user(user_id: str) -> None:
    with _LOCK:
        data = _load()
        u = _find(data, user_id)
        admins = [x for x in data["users"] if x.get("is_admin") and not x.get("disabled")]
        if u.get("is_admin") and len(admins) <= 1:
            raise AuthError("不能删除最后一个管理员")
        data["users"] = [x for x in data["users"] if x["id"] != user_id]
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if s.get("user_id") != user_id}
        _save(data)


def logout_all(user_id: str) -> None:
    with _LOCK:
        data = _load()
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if s.get("user_id") != user_id}
        _save(data)


# ---------------- 注册密钥（多密钥，2026-11-02 用户拍板） ----------------

def get_registration_keys() -> list[dict]:
    """全部在册密钥（[{key, created_at}...]，按生成顺序）。"""
    with _LOCK:
        out = []
        for e in _load().get("registration_keys") or []:
            if isinstance(e, dict) and e.get("key"):
                out.append({"key": e["key"],
                            "created_at": e.get("created_at") or ""})
            elif isinstance(e, str) and e:
                out.append({"key": e, "created_at": ""})
        return out


def get_registration_key() -> str | None:
    """首个在册密钥（兼容旧调用方；等价"注册是否开启"的取值视图）。"""
    keys = get_registration_keys()
    return keys[0]["key"] if keys else None


def generate_registration_key() -> str:
    """新增一把密钥（可同时存在多把，互不影响），返回明文。"""
    with _LOCK:
        data = _load()
        token = secrets.token_urlsafe(24)
        data.setdefault("registration_keys", []).append(
            {"key": token, "created_at": _now_iso()})
        _save(data)
        return token


def revoke_registration_key(key: str | None = None) -> None:
    """吊销：传 key=只废那一把；不传=全部关闭（注册关闭）。"""
    with _LOCK:
        data = _load()
        if key is None:
            data["registration_keys"] = []
        else:
            data["registration_keys"] = [
                e for e in data.get("registration_keys") or []
                if (e.get("key") if isinstance(e, dict) else e) != key]
        _save(data)


# ---------------- 限次（进程内存态） ----------------

def check_limited(kind: str, key: str) -> bool:
    k = f"{kind}:{key}"
    now = time.monotonic()
    with _RL_LOCK:
        hits = [t for t in _failures.get(k, []) if now - t < _FAIL_WINDOW_SEC]
        _failures[k] = hits
        return len(hits) >= _FAIL_MAX


def record_failure(kind: str, key: str) -> None:
    k = f"{kind}:{key}"
    now = time.monotonic()
    with _RL_LOCK:
        hits = [t for t in _failures.get(k, []) if now - t < _FAIL_WINDOW_SEC]
        hits.append(now)
        _failures[k] = hits


def bypass_user() -> dict:
    return {"id": "u_test", "username": "testuser", "is_admin": True,
            "disabled": False, "perms": {k: True for k in PERMS}}


# ---------------- 服务令牌（定时链脚本 HTTP 穿透，任务 13） ----------------

_SERVICE_TOKEN_PATH = PROJECT_ROOT / "data" / "auth_service_token"


def service_token() -> str:
    """服务令牌（data/auth_service_token，secrets.token_urlsafe(32)，ASCII）。

    文件缺失时自动生成落盘（原子写，父目录 mkdir）；存在则读取 strip 后返回。
    **每次现读文件**（无模块级缓存）：外部轮换即时生效，链脚本调用低频、IO 可忽略。

    安全说明（规格 §11）：浏览器跨站伪造受限——自定义头触发 CORS 预检，而
    OPTIONS 被认证闸 401（既有行为）→ 实际请求发不出；同源恶意脚本读不到
    data/ 下的令牌文件。令牌等同 admin 凭证，落盘保护与 data/auth.json 同级。
    """
    with _LOCK:
        try:
            if _SERVICE_TOKEN_PATH.exists():
                tok = _SERVICE_TOKEN_PATH.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        except OSError:
            pass
        tok = secrets.token_urlsafe(32)
        _SERVICE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SERVICE_TOKEN_PATH.with_suffix(".tmp")
        tmp.write_text(tok, encoding="utf-8")
        tmp.replace(_SERVICE_TOKEN_PATH)
        return tok


def check_service_token(header_value: str | None) -> bool:
    """校验 X-Service-Token 头。fail-closed：文件读不到/头为空/文件空 → False。"""
    if not header_value:
        return False
    try:
        expected = _SERVICE_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not expected:
        return False
    try:
        # bytes 版 compare_digest：str 版遇非 ASCII 会抛 TypeError（同 register_user）
        return secrets.compare_digest(expected.encode("utf-8"),
                                      header_value.encode("utf-8"))
    except (UnicodeEncodeError, TypeError):
        return False


def service_user() -> dict:
    """服务令牌对应的合成 admin（定时链脚本专用，无登录态、无落盘用户）。"""
    return {"id": "u_service", "username": "service", "is_admin": True,
            "disabled": False, "perms": {k: True for k in PERMS}}


# ---------------- 会话 ----------------

def create_session(user_id: str, days: int | None = None) -> str:
    if days is None:
        days = _session_days()
    token = secrets.token_urlsafe(32)
    with _LOCK:
        data = _load()
        # 顺手清理过期会话
        now = time.time()
        data["sessions"] = {t: s for t, s in data["sessions"].items()
                            if _epoch(s.get("expires_at", "")) > now}
        data["sessions"][token] = {
            "user_id": user_id,
            "expires_at": (datetime.now(_BJT) + timedelta(days=days)).isoformat(timespec="seconds"),
        }
        _save(data)
    return token


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def validate_session(token: str | None) -> dict | None:
    if not token:
        return None
    with _LOCK:
        data = _load()
        sess = data["sessions"].get(token)
        if not sess or _epoch(sess.get("expires_at", "")) <= time.time():
            return None
        for u in data["users"]:
            if u["id"] == sess.get("user_id") and not u.get("disabled"):
                return _public(u)
        return None


def logout(token: str) -> None:
    with _LOCK:
        data = _load()
        data["sessions"].pop(token, None)
        _save(data)


def _session_days() -> int:
    from .config import load_config
    try:
        return int(load_config().get("auth", {}).get("session_days", 30))
    except Exception:  # noqa: BLE001 — 配置坏不阻断登录
        return 30


# ---------------- 权限与配额 ----------------

def has_perm(user: dict | None, key: str) -> bool:
    if not user:
        return False
    if user.get("is_admin"):
        return True
    return bool((user.get("perms") or {}).get(key))


def ai_limit() -> int:
    from .config import load_config
    try:
        return int(load_config().get("auth", {}).get("ai_daily_limit", 50))
    except Exception:  # noqa: BLE001
        return 50


def ai_quota(user: dict) -> dict:
    limit = ai_limit()
    if user.get("is_admin") or limit <= 0:
        return {"limit": limit, "used": 0, "date": _today(), "allowed": True}
    with _LOCK:
        data = _load()
        for u in data["users"]:
            if u["id"] == user["id"]:
                usage = u.get("ai_usage") or {}
                used = usage.get("count", 0) if usage.get("date") == _today() else 0
                return {"limit": limit, "used": used, "date": _today(),
                        "allowed": used < limit}
    return {"limit": limit, "used": 0, "date": _today(), "allowed": True}


def bump_ai_usage(user_id: str) -> None:
    with _LOCK:
        data = _load()
        for u in data["users"]:
            if u["id"] == user_id:
                usage = u.get("ai_usage") or {}
                if usage.get("date") != _today():
                    usage = {"date": _today(), "count": 0}
                usage["count"] = int(usage.get("count", 0)) + 1
                u["ai_usage"] = usage
                _save(data)
                return
