# astock_hub/tq/gateway.py
"""TdxQuant 本地 HTTP 客户端（POST 127.0.0.1:17709，JSON {id, method, params}）。

返回统一结构：成功 {"ok": True, "data": {...}}；
失败 {"ok": False, "status": "unavailable"|"error", "error": {"message", "hint"}}。
只封装、不加风控——闸口在 risk.py，幂等在 ledger.py。
"""
from __future__ import annotations

import itertools
import threading

from .config import load_tq_config

_ids = itertools.count(1)
_ACCOUNT_LOCK = threading.Lock()
_ACCOUNT_ID: int | None = None

_CONNECT_HINT = "通达信客户端未启动或未开启 TQ 策略（确认客户端已登录、菜单已开启TQ策略、端口 17709 监听中）"


def _cfg() -> dict:
    from ..config import load_config
    return load_tq_config(load_config())


def tq_call(method: str, params: dict, *, timeout: float | None = None) -> dict:
    import httpx
    c = _cfg()
    try:
        resp = httpx.post(
            c["base_url"],
            json={"id": next(_ids), "method": method, "params": params},
            timeout=timeout or c["timeout_sec"],
        )
        payload = resp.json()
    except Exception as e:  # noqa: BLE001 — 网络层全部归一为 unavailable
        return {"ok": False, "status": "unavailable",
                "error": {"message": str(e), "hint": _CONNECT_HINT}}
    result = payload.get("result") or {}
    if str(result.get("ErrorId", "0")) != "0":
        msg = result.get("Error") or result.get("Msg") or str(result)
        hint = "在通达信客户端登录交易模块后重试" if "未登录" in msg else ""
        return {"ok": False, "status": "error", "error": {"message": msg, "hint": hint}}
    return {"ok": True, "data": result}


def _reset_account_cache() -> None:
    global _ACCOUNT_ID
    with _ACCOUNT_LOCK:
        _ACCOUNT_ID = None


def get_account_id_result() -> dict:
    """取资金账户句柄（缓存）。未登录/失败返回结构化错误。"""
    global _ACCOUNT_ID
    with _ACCOUNT_LOCK:
        if _ACCOUNT_ID is not None:
            return {"ok": True, "data": _ACCOUNT_ID}
    out = tq_call("stock_account", {"account": "", "account_type": "STOCK"})
    if not out["ok"]:
        if "未登录" in out["error"]["message"]:
            out["error"]["hint"] = "请先在通达信客户端登录交易模块（点交易→资金账号登录）"
        return out
    with _ACCOUNT_LOCK:
        _ACCOUNT_ID = int(out["data"]["Value"])
        return {"ok": True, "data": _ACCOUNT_ID}


def get_account_id() -> int:
    out = get_account_id_result()
    if not out["ok"]:
        raise RuntimeError(out["error"]["message"])
    return out["data"]


def get_asset() -> dict:
    return tq_call("query_stock_asset", {"account_id": get_account_id()})


def _unpack_rows(out: dict) -> dict:
    """列表查询拆箱：真实 TQ result = {"ErrorId":"0","Value":[行,...]}，
    整包透传会让调用方迭代 dict 的键（真机下单必炸）。失败信封原样返回。"""
    if not out.get("ok"):
        return out
    return {"ok": True, "data": out["data"].get("Value", [])}


def get_positions() -> dict:
    return _unpack_rows(tq_call("query_stock_positions", {"account_id": get_account_id()}))


def get_orders() -> dict:
    return _unpack_rows(tq_call("query_stock_orders", {"account_id": get_account_id()}))


def get_snapshot(code: str) -> dict:
    return tq_call("get_market_snapshot", {"stock_code": code, "field_list": []})


_ORDER_STATE = {0: "rejected", 1: "pending_confirm", 2: "submitted"}


def place_order(code: str, *, side: str, amount: int, price: float,
                notify: int | None = None) -> dict:
    """下单（限价，price_type=0 自填价）。Value 语义：0失败/1待客户端确认/2成功。

    notify: TQ 的"是否手动确认"参数（None=不传走客户端默认；0/1 语义待实测）。
    """
    params = {
        "account_id": get_account_id(),
        "stock_code": code,
        "order_type": 0 if side == "buy" else 1,
        "order_volume": int(amount),
        "price_type": 0,
        "price": float(price),
    }
    if notify is not None:
        params["notify"] = int(notify)
    out = tq_call("order_stock", params)
    if not out["ok"]:
        return out
    r = out["data"]
    v = int(r.get("Value", 0))
    return {"ok": True, "tq_value": v, "state": _ORDER_STATE.get(v, "rejected"),
            "wtbh": r.get("Wtbh") or "", "msg": r.get("Msg", "")}


def cancel_order(code: str, wtbh: str) -> dict:
    out = tq_call("cancel_order_stock",
                  {"account_id": get_account_id(), "stock_code": code, "order_id": wtbh})
    if not out["ok"]:
        return out
    if int(out["data"].get("Value", 0)) != 1:
        return {"ok": False, "status": "error",
                "error": {"message": out["data"].get("Msg") or "撤单失败", "hint": ""}}
    return {"ok": True, "msg": out["data"].get("Msg", "")}
