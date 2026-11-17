# astock_hub/webapi_auth.py
"""认证与账号管理端点。Cookie 名 ah_session；错误体统一 {"error": 中文}。"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import auth as _auth

router = APIRouter(tags=["auth"])

SESSION_COOKIE = "ah_session"

# bootstrap 首跑建管理员只收 loopback/私网源：面板默认 bind 0.0.0.0（2026-11-01
# 起手机可达），新部署在机主打开页面前，公网扫描器不能自建永久管理员。
# 局域网（手机）仍可用；上云后机主先本机/隧道 bootstrap 再开放——正是想要的顺序。
_BOOTSTRAP_ALLOWED_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _bootstrap_source_allowed(host: str | None) -> bool:
    """源地址是否允许 bootstrap。非 IP 主机名/空值一律拒（fail-closed）。"""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped                    # ::ffff:192.168.1.5 → 192.168.1.5
    return any(ip in net for net in _BOOTSTRAP_ALLOWED_NETS)


def _json(data: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status_code)


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/auth/session")
def api_auth_session(request: Request):
    user = _auth.validate_session(request.cookies.get(SESSION_COOKIE))
    # features：面板功能开关（云端 [tq].enabled=false → 前端隐藏交易入口，
    # 2026-11-03 用户拍板方案A——TQ 仅本机 Windows，交易只在本机面板做）
    from .config import load_config
    tq_on = bool((load_config().get("tq") or {}).get("enabled"))
    return {"user": user, "need_bootstrap": _auth.need_bootstrap(),
            "features": {"tq": tq_on}}


@router.get("/api/auth/bootstrap-status")
def api_auth_bootstrap_status():
    return {"needed": _auth.need_bootstrap()}


@router.post("/api/auth/bootstrap")
def api_auth_bootstrap(request: Request, payload: dict):
    # 源地址闸在最前：公网扫描器连"系统已初始化"都不给看
    if not _bootstrap_source_allowed(_client_host(request)):
        return _json({"error": "forbidden", "required": "loopback_or_lan"},
                     status_code=403)
    if not _auth.need_bootstrap():
        return _json({"error": "系统已初始化"}, status_code=403)
    try:
        user = _auth.bootstrap_admin(str(payload.get("username") or ""),
                                     str(payload.get("password") or ""))
    except _auth.AuthError as e:
        return _json({"error": str(e)}, status_code=400)
    token = _auth.create_session(user["id"])
    resp = _json({"user": user})
    _set_cookie(resp, token)
    return resp


@router.post("/api/auth/register")
def api_auth_register(request: Request, payload: dict):
    if _auth.check_limited("register", _client_host(request)):
        return _json({"error": "尝试过于频繁，请 10 分钟后再试"}, status_code=429)
    try:
        user = _auth.register_user(str(payload.get("username") or ""),
                                   str(payload.get("password") or ""),
                                   str(payload.get("invite_key") or ""))
    except _auth.AuthError as e:
        _auth.record_failure("register", _client_host(request))
        status = 403 if "密钥" in str(e) else 400
        return _json({"error": str(e)}, status_code=status)
    token = _auth.create_session(user["id"])        # 注册即登录
    resp = _json({"user": user})
    _set_cookie(resp, token)
    return resp


@router.post("/api/auth/login")
def api_auth_login(request: Request, payload: dict):
    username = str(payload.get("username") or "")
    if _auth.check_limited("login", username):
        return _json({"error": "尝试过于频繁，请 10 分钟后再试"}, status_code=429)
    try:
        user = _auth.authenticate(username, str(payload.get("password") or ""))
    except _auth.AuthError as e:
        # auth.json 损坏等存储异常：fail-closed 中文错误体，不抛 500 堆栈
        # （register/bootstrap 都已 catch，登录口补上同款）
        return _json({"error": f"认证存储异常，请检查 data/auth.json（{e}）"},
                     status_code=500)
    if user is None:
        _auth.record_failure("login", username)
        return _json({"error": "用户名或密码错误"}, status_code=401)
    token = _auth.create_session(user["id"])
    resp = _json({"user": user})
    _set_cookie(resp, token)
    return resp


@router.post("/api/auth/logout")
def api_auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _auth.logout(token)
    resp = _json({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def _set_cookie(resp: JSONResponse, token: str) -> None:
    days = 30
    try:
        from .config import load_config
        days = int(load_config().get("auth", {}).get("session_days", 30))
    except Exception:  # noqa: BLE001
        pass
    resp.set_cookie(SESSION_COOKIE, token, max_age=days * 86400,
                    httponly=True, samesite="lax", path="/")


# ---------------- /api/accounts 管理端点（任务 5） ----------------

@router.get("/api/accounts")
def api_accounts_list(request: Request):
    user = request.state.user
    if not user.get("is_admin"):
        return _json({"error": "forbidden", "required": "admin"}, status_code=403)
    keys = _auth.get_registration_keys()
    return {"users": _auth.list_users(),
            "registration_key_set": bool(keys),
            "registration_keys": keys}


@router.post("/api/accounts/registration-key")
def api_accounts_registration_key(request: Request, payload: dict):
    """注册密钥管理（多密钥，2026-11-02 用户拍板"可以生成多个"）。

    - generate：新增一把（互不影响，可同时多把有效）
    - rotate：全废并出新一把（兼容旧"轮换"语义）
    - revoke：带 key=只废那一把；不带 key=全部关闭（注册关闭）
    """
    user = request.state.user
    if not user.get("is_admin"):
        return _json({"error": "forbidden", "required": "admin"}, status_code=403)
    action = payload.get("action")
    if action in ("generate", "rotate"):
        if action == "rotate":
            _auth.revoke_registration_key()          # 全废
        return {"key": _auth.generate_registration_key()}
    if action == "revoke":
        one = payload.get("key")
        if one:
            _auth.revoke_registration_key(str(one))
        else:
            _auth.revoke_registration_key()
        return {"key": None, "remaining": len(_auth.get_registration_keys())}
    return _json({"error": "action 必须是 generate/rotate/revoke"}, status_code=400)


@router.patch("/api/accounts/{user_id}")
def api_accounts_patch(request: Request, user_id: str, payload: dict):
    user = request.state.user
    if not user.get("is_admin"):
        return _json({"error": "forbidden", "required": "admin"}, status_code=403)
    try:
        if "perms" in payload:
            updated = _auth.set_perms(user_id, payload["perms"])
        else:
            updated = _auth.get_user(user_id)
            if updated is None:
                raise _auth.AuthError("账号不存在")
        if "disabled" in payload:
            if user_id == user["id"] and payload["disabled"]:
                raise _auth.AuthError("不能停用自己")
            _auth.set_disabled(user_id, bool(payload["disabled"]))
            updated = _auth.get_user(user_id)
    except _auth.AuthError as e:
        return _json({"error": str(e)}, status_code=400)
    return {"user": updated}


@router.post("/api/accounts/{user_id}/reset-password")
def api_accounts_reset_password(request: Request, user_id: str, payload: dict):
    user = request.state.user
    if not user.get("is_admin"):
        return _json({"error": "forbidden", "required": "admin"}, status_code=403)
    try:
        _auth.reset_password(user_id, str(payload.get("password") or ""))
    except _auth.AuthError as e:
        return _json({"error": str(e)}, status_code=400)
    return {"ok": True}


@router.delete("/api/accounts/{user_id}")
def api_accounts_delete(request: Request, user_id: str):
    user = request.state.user
    if not user.get("is_admin"):
        return _json({"error": "forbidden", "required": "admin"}, status_code=403)
    if user_id == user["id"]:
        return _json({"error": "不能删除自己"}, status_code=400)
    try:
        _auth.delete_user(user_id)
    except _auth.AuthError as e:
        return _json({"error": str(e)}, status_code=400)
    return {"ok": True}
