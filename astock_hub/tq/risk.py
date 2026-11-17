# astock_hub/tq/risk.py
"""下单前 9 道闸口（纯函数，无 IO；调用方负责组装 ctx）。

剔除铁律与主线推送同口径（2026-10-21 拍板）：北交所 + ST/*ST 手动也不豁免。
"""
from __future__ import annotations

from ..symbols import is_a_share_stock


def board_pct(code6: int | str) -> float:
    s = str(code6)
    return 0.20 if s.startswith(("30", "68")) else 0.10


def limit_prices(code6: int | str, last_close: float) -> tuple[float, float]:
    p = board_pct(code6)
    return round(last_close * (1 + p) + 1e-9, 2), round(last_close * (1 - p) + 1e-9, 2)


def check_order(req: dict, ctx: dict) -> list[str]:
    """全部通过返回 []，否则返回逐条拒绝原因（中文，直接给用户看）。"""
    problems: list[str] = []
    if not ctx["enabled"]:
        problems.append("[tq] enabled=false，通道总开关未开")
    if ctx["kill_active"]:
        problems.append("总闸已关闭（data/tq/kill.flag 存在），所有下单被拦截")
    if not ctx["is_trading"]:
        problems.append("不在 A 股交易时段（连续竞价 9:30-11:30 / 13:00-15:00）")
    code = req["code"]
    bare = code.split(".")[0]
    name = ctx.get("name") or ""
    if not is_a_share_stock(code):
        problems.append(f"{code} 不是A股股票")
    elif bare.startswith(("920", "43", "83", "87")):
        # 铁律对裸码同样生效（只拦 .BJ 后缀会被 "920001" 这类无后缀码绕过）；
        # 前缀口径与 manual_push._bare_to_ts 一致（920/43/83/87→北交所）。
        problems.append("北交所品种不纳入自动交易（铁律）")
    if "ST" in name[:4]:
        # 覆盖 ST/*ST/S*ST/SST 全部历史前缀（startswith 两档会漏 S*ST/SST）。
        problems.append("ST/*ST 不纳入自动交易（铁律）")
    if not problems:  # 价格类闸口需要有效的代码/时段前提，前面挂了就不用再算
        up, down = limit_prices(code.split(".")[0], ctx["last_close"])
        if req["side"] == "buy" and req["price"] >= up:
            problems.append(f"买入价 {req['price']} 触及涨停价 {up}，不追涨停")
        if req["side"] == "sell" and req["price"] <= down:
            problems.append(f"卖出价 {req['price']} 触及跌停价 {down}，不砸跌停")
        now = ctx.get("now_price") or 0
        if now > 0 and abs(req["price"] - now) / now > ctx["slip_pct"]:
            problems.append(f"限价 {req['price']} 偏离现价 {now} 超过 ±{ctx['slip_pct']:.0%}")
    amt = int(req["amount"])
    if code.startswith("688"):
        if amt < 200 or (amt > 200 and amt % 100 != 0):
            problems.append("科创板数量须为200股起或200股以上整百")
    elif amt % 100 != 0:
        problems.append("数量须为100股整数倍")
    cost = req["price"] * amt
    if cost > ctx["single_order_max_amount"]:
        problems.append(f"单笔金额 {cost:.0f} 元超单笔上限 {ctx['single_order_max_amount']} 元")
    if ctx.get("equity"):
        if cost > ctx["equity"] * ctx.get("position_max_pct", 1.0):
            problems.append(f"单票金额超总资产 {ctx.get('position_max_pct', 1.0):.0%} 上限")
    if ctx["today_orders"] >= ctx["daily_max_orders"]:
        problems.append(f"当日委托笔数已达上限 {ctx['daily_max_orders']} 笔")
    if req["side"] == "buy" and cost > ctx["cash"]:
        problems.append(f"资金不足：可用 {ctx['cash']:.0f} 元 < 需要 {cost:.0f} 元")
    if req["side"] == "sell" and amt > ctx["can_use"].get(bare, 0):
        # can_use 键统一裸码（webapi 组 ctx 时 split 过）——查询同样用裸码，
        # 消除对 TQ 返回 Code 带不带后缀的格式假设。
        problems.append(f"可卖数量 {ctx['can_use'].get(bare, 0)} 不足 {amt}")
    return problems
