# astock_hub/webapi_tq.py
"""交易通道 API —— 路由前缀 /api/tq。

GET  /status      通道状态（开关/总闸/账户连通）
GET  /asset       资金    GET /positions 持仓    GET /orders 当日委托
POST /order       手动下单（9 道闸口+账本+总闸）
POST /cancel      撤单    POST /kill  总闸开关
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from .config import load_config
from .market.sessions import market_session
from .tq import gateway, ledger
from .tq.config import load_tq_config
from .tq.gateway import (
    get_account_id_result,
    get_asset,
    get_orders,
    get_positions,
)

router = APIRouter(prefix="/api/tq", tags=["tq"])


def _cfg() -> dict:
    return load_tq_config(load_config())


def _unavailable(msg: str, hint: str) -> dict:
    return {"status": "unavailable", "error": {"message": msg, "hint": hint}}


def _wrap(out: dict) -> dict:
    if out.get("ok"):
        return {"status": "ok", "data": out["data"]}
    err = out.get("error") or {}
    return _unavailable(err.get("message", ""), err.get("hint", ""))


def _read_query(fn) -> dict:
    """只读查询统一出口：句柄闸门 + 结构化降级，绝不 500。

    gateway 的 get_asset/get_positions/get_orders 内部先求值 get_account_id()，
    句柄失败（客户端未启动/交易模块未登录）时它 raise RuntimeError——
    必须在端点内拦下，把富化 hint 透传给前端。
    """
    try:
        acc = get_account_id_result()
        if not acc.get("ok"):
            err = acc.get("error") or {}
            return _unavailable(err.get("message", ""), err.get("hint", ""))
        return _wrap(fn())
    except Exception as e:  # noqa: BLE001 —— 只读端点绝不 500
        return _unavailable(str(e), "通达信客户端未启动或未开启 TQ 策略")


def _with_names(out: dict) -> dict:
    """给行数据补股票名称（查不到留空串，绝不因此炸端点）。"""
    if out.get("status") == "ok" and isinstance(out.get("data"), list):
        try:
            cfg = load_config()
            for r in out["data"]:
                if isinstance(r, dict) and r.get("Code"):
                    r["Name"] = _name_of(cfg, r["Code"])
        except Exception:  # noqa: BLE001 — 名称是增强信息，失败保持无 Name
            pass
    return out


def _enrich_positions(out: dict) -> dict:
    """持仓补现价/最新市值/浮动盈亏/盈亏比例（对齐通达信持仓页字段）。

    每只取一次本地 TQ 快照；单只失败跳过不炸，行保留其余字段。
    """
    if out.get("status") == "ok" and isinstance(out.get("data"), list):
        for r in out["data"][:30]:  # 持仓通常个位数，上限防打爆轮询
            code = r.get("Code")
            if not code:
                continue
            # 派生字段不依赖行情：当前成本、未可用（在途+冻结合计，TQ 持仓行不分列）
            try:
                vol = float(r.get("TotalVol") or 0)
                cbj = float(r.get("Cbj") or 0)
                can = float(r.get("CanUseVol") or 0)
                r["CostAmt"] = round(vol * cbj, 2)
                r["Unavail"] = max(0.0, vol - can)
            except Exception:  # noqa: BLE001 — 数值解析失败跳过派生
                pass
            try:
                snap = gateway.get_snapshot(code)
                if not snap.get("ok"):
                    continue
                d = snap.get("data") or {}
                now = float(d.get("Now") or 0)
                vol = float(r.get("TotalVol") or 0)
                cbj = float(r.get("Cbj") or 0)
                if now > 0 and vol > 0:
                    r["Now"] = now
                    r["MktValue"] = round(now * vol, 2)
                    r["Pnl"] = round((now - cbj) * vol, 2)
                    r["PnlPct"] = round((now - cbj) / cbj * 100, 2) if cbj > 0 else None
            except Exception:  # noqa: BLE001 — 行情是增强信息，失败保持原行
                continue
    return out


@router.get("/status")
def status():
    c = _cfg()
    kill = Path(c["kill_flag"]).exists()
    acc = get_account_id_result()
    return {"enabled": c["enabled"], "kill_active": kill,
            "account_ok": acc.get("ok", False),
            "account_error": None if acc.get("ok") else acc.get("error", {})}


@router.get("/asset")
def asset():
    return _read_query(get_asset)


@router.get("/positions")
def positions():
    return _enrich_positions(_with_names(_read_query(get_positions)))


@router.get("/orders")
def orders():
    """当日委托：TQ 撤单后 Status 仍为 0 与挂单撞车分不清——用账本撤单记录过滤。"""
    out = _read_query(get_orders)
    if out.get("status") == "ok" and isinstance(out.get("data"), list):
        c = _cfg()
        try:
            cancelled = {r.get("wtbh") for r in ledger.iter_recent(c["ledger_dir"], 500)
                         if r.get("state") == "cancelled" and r.get("wtbh")}
            out["data"] = [r for r in out["data"] if r.get("Wtbh") not in cancelled]
        except Exception:  # noqa: BLE001 — 过滤失败保留原列表，不炸端点
            pass
    return _with_names(out)


@router.get("/quote")
def quote(code: str):
    """下单卡行情条：6 位代码归一 → 名称/现价/涨跌/买卖一。只读，不下单。"""
    try:
        from .symbols import is_a_share_stock
        raw = (code or "").strip()
        if re.fullmatch(r"\d{6}", raw):
            d0 = raw[0]
            suffix = "SH" if d0 == "6" else "SZ" if d0 in ("0", "3") else "BJ"
            full = f"{raw}.{suffix}"
        else:
            full = raw.upper()
        bare = full.split(".")[0]
        if bare.startswith(("920", "43", "83", "87")) or full.endswith(".BJ"):
            return {"status": "rejected", "problems": ["北交所不支持自动交易"]}
        if not is_a_share_stock(full):
            return {"status": "rejected", "problems": ["不是 A 股股票代码"]}
        snap = gateway.get_snapshot(full)
        if not snap.get("ok"):
            err = snap.get("error") or {}
            return {"status": "unavailable",
                    "error": {"message": err.get("message", "行情不可用"),
                              "hint": err.get("hint", "通达信客户端未启动或未开启 TQ 策略")}}
        d = snap.get("data") or {}
        now = float(d.get("Now") or 0)
        last_close = float(d.get("LastClose") or 0)
        bid = d.get("Buyp") or []
        ask = d.get("Sellp") or []
        buy_v = d.get("Buyv") or []
        sell_v = d.get("Sellv") or []

        def _f(v):
            try:
                return float(v) if v not in (None, "", "0.00", "0", 0) else None
            except (TypeError, ValueError):
                return None

        def _vol(v):
            try:
                return int(float(v)) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        bids = [_f(x) for x in bid]
        asks = [_f(x) for x in ask]
        bid_vols = [_vol(x) for x in buy_v]
        ask_vols = [_vol(x) for x in sell_v]
        # 委比 = (五档委买总量 - 五档委卖总量) / (委买+委卖) × 100
        bs = sum(v for v in bid_vols if v)
        ss = sum(v for v in ask_vols if v)
        weibi = round((bs - ss) / (bs + ss) * 100, 2) if (bs + ss) > 0 else None
        data = {
            "code": full,
            "name": _name_of(load_config(), full),
            "now": now or None,
            "last_close": last_close or None,
            "pct": round((now - last_close) / last_close * 100, 2) if now > 0 and last_close > 0 else None,
            "bid": bids[0] if bids else None,
            "ask": asks[0] if asks else None,
            "bids": bids,
            "asks": asks,
            "bid_vols": bid_vols,
            "ask_vols": ask_vols,
            "weibi": weibi,
        }
        return {"status": "ok", "data": data}
    except Exception as e:  # noqa: BLE001 — 绝不 500
        return {"status": "error", "error": {"message": str(e), "hint": ""}}


# ---- 下单/撤单/总闸（任务 6：全链路闸口） ----
import threading

from . import manual_push as _mp          # 复用主线推送同款名称缓存（ST 口径一致）
from .tq import risk as _risk

# 裁定1（任务 4 审查）：下单临界区（intent 查重→place_order→记账）模块级锁串行化——
# FastAPI 同步 handler 走线程池，并发请求可绕过纯函数账本判重（TOCTOU 双花）。
_ORDER_LOCK = threading.Lock()


def _name_of(cfg: dict, code: str) -> str:
    return _mp._name_of(cfg, code)


def _trading_day_ok() -> bool:
    """交易日闸（market_session 只判工作日+时段，法定节假日会放行）。

    复用 chain_guard._is_trading_day 语义：日历明确覆盖的周末/节假日返回
    False（拒单）；日历不可用/陈旧未覆盖按交易日放行（宁可漏拦不可错杀
    新交易日，与告警侧同一新鲜度口径）。
    """
    from .chain_guard import _is_trading_day
    return _is_trading_day()


def _check_order(req: dict, ctx: dict) -> list[str]:
    return _risk.check_order(req, ctx)


def _reject(c: dict, req: dict, problems: list[str]) -> dict:
    """闸口/校验拒单：账本留痕 + 结构化返回（审计可查）。"""
    ledger.record(c["ledger_dir"], {**req, "state": "rejected", "problems": problems})
    return {"status": "rejected", "problems": problems}


class OrderBody(BaseModel):
    side: str
    code: str
    price: float
    amount: int
    intent_id: str


@router.post("/order")
def place(body: OrderBody):
    try:
        # 裁定1：整段下单链路（查重→闸口→下单→记账）持锁串行。
        with _ORDER_LOCK:
            return _place_locked(body)
    except Exception as e:  # noqa: BLE001 —— 绝不 500（裁定 3）
        return {"status": "error", "error": {"message": str(e), "hint": ""}}


def _place_locked(body: OrderBody) -> dict:
    c = _cfg()
    req = {"side": body.side, "code": body.code, "price": body.price,
           "amount": body.amount, "intent_id": body.intent_id}
    # 审查Critical：side 白名单——gateway 对非 "buy" 一律当下卖单（order_type=1），
    # risk 层跌停/资金/可卖三闸会静默放行，必须在端点层先拦。
    if body.side not in ("buy", "sell"):
        return _reject(c, req, ["side 须为 buy 或 sell"])
    # 终审：intent_id 必填——空串会让幂等查重静默失效（is_submitted("") 语义不可靠）。
    if not body.intent_id.strip():
        return _reject(c, req, ["intent_id 必填"])
    # 裁定2①：amount 必须为正整数（组 ctx 前；0/负数会绕过整手闸，直接拒）。
    if (not isinstance(body.amount, int) or isinstance(body.amount, bool)
            or body.amount <= 0):
        return _reject(c, req, ["数量须为正整数"])
    # 终审：price 必须为正有限数（与 amount 同款端点层校验；NaN/inf 会穿透
    # risk 的涨跌停/滑点比较成为比较盲区）。
    if not math.isfinite(body.price) or body.price <= 0:
        return _reject(c, req, ["价格须为正数"])
    # 总闸前置拦截（独立于 risk.check_order：面板关闸即刻生效，不依赖 9 闸复算）。
    if Path(c["kill_flag"]).exists():
        return _reject(c, req, ["总闸已关闭（data/tq/kill.flag 存在），所有下单被拦截"])
    _, is_trading = market_session(datetime.now())
    # 终审：节假日日历闸——market_session 只判工作日+时段，法定节假日（如国庆）
    # 周中会误判为交易时段，叠加本地交易日历拦下（陈旧/不可用日历按交易日放行）。
    if not _trading_day_ok():
        return _reject(c, req, ["非交易日，不接受委托"])
    if body.intent_id and ledger.is_submitted(c["ledger_dir"], body.intent_id):
        return {"status": "duplicate", "msg": "该 intent_id 已提交过，拒绝重复下单"}
    # 终审：ST 铁律 fail-closed——名称解析失败（空串）宁可拒单，不可放行 ST。
    name = (_name_of(load_config(), body.code) or "").strip()
    if not name:
        return _reject(c, req, ["无法核实股票名称，拒单"])
    acc = get_account_id_result()
    if not acc.get("ok"):
        return {"status": "unavailable", "error": acc.get("error", {})}
    cash = equity = 0.0
    can_use: dict[str, int] = {}
    now_price = last_close = 0.0
    a = gateway.get_asset()
    if a.get("ok"):
        cash = float(a["data"].get("Cash") or 0)
        equity = float(a["data"].get("CurrentEquity") or 0)
    p = gateway.get_positions()
    if p.get("ok"):
        # 键统一裸码（split 去后缀）——消除对 TQ 返回 Code 带不带 .SZ/.SH 的格式假设
        #（risk 侧查询同用裸码，两端同一口径）。
        can_use = {r["Code"].split(".")[0]: int(float(r.get("CanUseVol") or 0)) for r in p["data"]}
    s = gateway.get_snapshot(body.code)
    if s.get("ok"):
        now_price = float(s["data"].get("Now") or 0)
        last_close = float(s["data"].get("LastClose") or 0)
    # 裁定2②：快照昨收缺失（<=0）→ 拒单——防 risk.py 涨跌停闸在 last_close=0 时误杀全部买单。
    if last_close <= 0:
        return _reject(c, req, ["行情数据不足：无法获取昨收价，拒单"])
    ctx = {"enabled": c["enabled"], "kill_active": Path(c["kill_flag"]).exists(),
           "is_trading": is_trading, "cash": cash, "equity": equity,
           "can_use": can_use, "now_price": now_price, "last_close": last_close,
           "name": name,
           "today_orders": ledger.count_today(c["ledger_dir"]),
           "single_order_max_amount": c["single_order_max_amount"],
           "daily_max_orders": c["daily_max_orders"], "slip_pct": c["price_slip_pct"]}
    problems = _check_order(req, ctx)
    if problems:
        return _reject(c, req, problems)
    # code 走关键字实参：真实签名 place_order(code, *, side, amount, price) 兼容，
    # 测试 fake 的 lambda **k 也兼容（简报实现按位置传 code 与其 fake 不一致）。
    out = gateway.place_order(code=body.code, side=body.side, amount=body.amount, price=body.price)
    # 审查重要②：下单后的记账单独兜底——单已提交而账没记上（磁盘满等）必须可识别，
    # 不许被外层 except 归一成 status=error（那会伪装成下单失败诱导重试）。
    ledger_error = None
    try:
        ledger.record(c["ledger_dir"], {**req, "state": out.get("state", "error"),
                                        "wtbh": out.get("wtbh", ""), "msg": out.get("msg", "")})
    except Exception as e:  # noqa: BLE001
        ledger_error = str(e)
    if not out.get("ok"):
        resp = {"status": "error", "error": out.get("error", {})}
        if ledger_error:
            resp["ledger_error"] = ledger_error
        return resp
    resp = {"status": out["state"], "wtbh": out.get("wtbh", ""), "msg": out.get("msg", "")}
    if ledger_error:
        resp["ledger_error"] = ledger_error
    return resp


class CancelBody(BaseModel):
    code: str
    wtbh: str


@router.get("/order-history")
def order_history(limit: int = 200):
    """历史委托：全日期账本回看（新在前）。撤单后当日委托可能消失，账本是权威记录。"""
    c = _cfg()
    try:
        rows = ledger.iter_recent(c["ledger_dir"], max(1, min(int(limit), 1000)))
        return {"status": "ok", "data": rows}
    except Exception as e:  # noqa: BLE001 — 绝不 500
        return _unavailable(str(e), "")


@router.post("/cancel")
def cancel(body: CancelBody):
    try:
        out = gateway.cancel_order(body.code, body.wtbh)
        if out.get("ok"):
            # 撤单留痕（2026-11-02）：撤成功的单会从当日委托消失，账本记一笔 cancelled
            try:
                ledger.record(_cfg()["ledger_dir"],
                              {"state": "cancelled", "code": body.code,
                               "wtbh": body.wtbh, "msg": out.get("msg", "")})
            except Exception:  # noqa: BLE001 — 留痕失败不影响撤单结果
                pass
            return {"status": "ok", "msg": out.get("msg", "")}
        # 审查重要①：失败透传 gateway 信封——unavailable vs 业务失败、message/hint 不丢
        #（gateway.cancel_order 失败分支没有 msg 键，只取 msg 会把原因全丢）。
        return {"status": out.get("status") or "error",
                "error": out.get("error") or {}, "msg": out.get("msg", "")}
    except Exception as e:  # noqa: BLE001 —— 绝不 500（裁定 3：get_account_id 系 raise 路径）
        return {"status": "error", "error": {"message": str(e), "hint": ""}}


class KillBody(BaseModel):
    enabled: bool


@router.post("/kill")
def kill(body: KillBody):
    try:
        c = _cfg()
        flag = Path(c["kill_flag"])
        flag.parent.mkdir(parents=True, exist_ok=True)
        if body.enabled:
            flag.write_text("closed by panel", encoding="utf-8")
        elif flag.exists():
            flag.unlink()
        return {"kill_active": flag.exists()}
    except Exception as e:  # noqa: BLE001 —— 绝不 500（裁定 3）
        return {"status": "error", "error": {"message": str(e), "hint": ""}}
