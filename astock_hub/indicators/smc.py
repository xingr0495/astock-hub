# -*- coding: utf-8 -*-
"""SMC 结构计算（第三层趋势执行的"能算的自动算"部分，P3）。

纯本地 OHLC 计算，不发任何请求。周线由日线聚合（每周最后一根的 OHLC）。

算法（v1，参数化便于后续回测调优）：
- 摆动点（pivot）：high[i] ≥ 左右各 k 根的 high（摆动低点对 low 对称），
  第 i+k 根收盘后确认（**天然滞后 k 根**，最新 k 根内的摆动点不成立）。
- 结构事件（按收盘价判，只用已确认的摆动点）：
  * 收盘突破最近摆动高点，且当前趋势为上 → **BOS↑**（顺势结构破坏，趋势延续）
  * 收盘跌破最近摆动低点，且当前趋势为下 → **BOS↓**
  * 收盘突破最近摆动高点，而当前趋势为下 → **CHOCH↑**（特性改变，趋势可能反转）
  * 收盘跌破最近摆动低点，而当前趋势为上 → **CHOCH↓**
  同一摆动点被突破后即"消费"，不重复触发；首个事件定初始趋势。
- 订单块（v1 近似）：最近一次结构突破前，逆向的最后一根 K 线区间
  （BOS↑ → 突破前 20 根内最后一根阴线的 low~high；BOS↓ 对称取阳线）。
  精确的"摆动低点→突破"冲击腿定位留给人工判读，这里给参考区。

输出不含交易建议——入场点/止损由手册结合 R:R 判读。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Pivot:
    idx: int
    kind: str      # "H" / "L"
    price: float
    consumed: bool = False


@dataclass
class Event:
    idx: int
    date: str
    type: str      # BOS_up / BOS_down / CHOCH_up / CHOCH_down
    price: float   # 突破参考价（收盘）
    broken_pivot: float


@dataclass
class Structure:
    trend: str | None = None          # up / down / None
    events: list[Event] = field(default_factory=list)
    last_sh: Pivot | None = None      # 最近未消费的摆动高/低点
    last_sl: Pivot | None = None


def find_pivots(highs: list[float], lows: list[float], k: int) -> list[Pivot]:
    """摆动高点：左侧严格更高、右侧不更低（平台取最左端）；低点对称。"""
    pivots: list[Pivot] = []
    n = len(highs)
    for i in range(k, n - k):
        hh = highs[i]
        if (all(hh > highs[j] for j in range(i - k, i))
                and all(hh >= highs[j] for j in range(i + 1, i + k + 1))):
            pivots.append(Pivot(i, "H", hh))
        ll = lows[i]
        if (all(ll < lows[j] for j in range(i - k, i))
                and all(ll <= lows[j] for j in range(i + 1, i + k + 1))):
            pivots.append(Pivot(i, "L", ll))
    return pivots


def analyze_structure(dates: list[str], opens: list[float], highs: list[float],
                      lows: list[float], closes: list[float],
                      k: int = 5) -> Structure:
    """逐 bar 扫结构事件。只用已确认（idx+k <= 当前 bar）的摆动点。"""
    n = len(closes)
    pivots = find_pivots(highs, lows, k)
    pivots.sort(key=lambda p: p.idx)
    st = Structure()
    pi = 0   # 待确认摆动点游标
    for i in range(n):
        # 确认摆动点（其右侧 k 根已走完）
        while pi < len(pivots) and pivots[pi].idx + k <= i:
            p = pivots[pi]
            if p.kind == "H":
                if st.last_sh is None or p.idx > st.last_sh.idx:
                    st.last_sh = p
            else:
                if st.last_sl is None or p.idx > st.last_sl.idx:
                    st.last_sl = p
            pi += 1
        c = closes[i]
        fired = None
        if st.last_sh and not st.last_sh.consumed and c > st.last_sh.price:
            fired = ("BOS_up" if st.trend in (None, "up") else "CHOCH_up",
                     st.last_sh)
        elif st.last_sl and not st.last_sl.consumed and c < st.last_sl.price:
            fired = ("BOS_down" if st.trend in (None, "down") else "CHOCH_down",
                     st.last_sl)
        if fired:
            etype, pivot = fired
            pivot.consumed = True
            # 消费后清空该侧引用：同侧须等新的摆动点确认才可能再触发，
            # 避免同一突破位重复报事件（对侧引用保留，供反向突破判 CHOCH）
            if pivot.kind == "H":
                st.last_sh = None
            else:
                st.last_sl = None
            st.trend = "up" if etype.endswith("up") else "down"
            st.events.append(Event(i, dates[i], etype, c, pivot.price))
    return st


def latest_order_block(opens: list[float], highs: list[float], lows: list[float],
                       closes: list[float], ev: Event, lookback: int = 20) -> dict | None:
    """最近一次突破的订单块参考区：突破前 lookback 根内最后一根逆向 K 线。"""
    lo = max(0, ev.idx - lookback)
    if ev.type.endswith("up"):
        for j in range(ev.idx - 1, lo - 1, -1):
            if closes[j] < opens[j]:     # 最后一根阴线
                return {"side": "bullish", "idx": j, "low": lows[j],
                        "high": highs[j], "close": closes[j]}
    else:
        for j in range(ev.idx - 1, lo - 1, -1):
            if closes[j] > opens[j]:     # 最后一根阳线
                return {"side": "bearish", "idx": j, "low": lows[j],
                        "high": highs[j], "close": closes[j]}
    return None


def resample_weekly(dates: list[str], opens: list[float], highs: list[float],
                    lows: list[float], closes: list[float]) -> tuple:
    """日线 → 周线（ISO 年-周分组，取当周首开/最高/最低/末收）。"""
    from datetime import date as _d
    buckets: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []
    for i, ds in enumerate(dates):
        dt = _d.fromisoformat(ds)
        key = dt.isocalendar()[:2]      # (iso_year, iso_week)
        if key not in buckets:
            buckets[key] = i
            order.append(key)
    d2, o2, h2, l2, c2, dates2 = [], [], [], [], [], []
    for key in order:
        start = buckets[key]
        # 该周在原序列中的结束 = 下一周起点前一根（日线按交易日连续）
        end = start
        nxt = order.index(key) + 1
        end = (buckets[order[nxt]] - 1) if nxt < len(order) else len(dates) - 1
        o2.append(opens[start])
        h2.append(max(highs[start:end + 1]))
        l2.append(min(lows[start:end + 1]))
        c2.append(closes[end])
        d2.append(dates[end])
    return d2, o2, h2, l2, c2


def ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n or n <= 0:
        return None
    return round(sum(closes[-n:]) / n, 3)


def smc_report_frame(dates: list[str], opens: list[float], highs: list[float],
                     lows: list[float], closes: list[float],
                     k: int = 5, max_events: int = 5) -> dict:
    """单周期的 SMC 结构摘要（日线/周线共用）。"""
    st = analyze_structure(dates, opens, highs, lows, closes, k)
    last_ev = st.events[-1] if st.events else None
    ob = latest_order_block(opens, highs, lows, closes, last_ev) if last_ev else None
    if ob and ob.get("idx") is not None:
        ob["date"] = dates[ob["idx"]]
        ob.pop("idx")
    return {
        "bars": len(closes),
        "pivot_k": k,
        "trend": st.trend,
        "ma20": ma(closes, 20), "ma60": ma(closes, 60),
        "close": closes[-1], "asof": dates[-1],
        "event_count": len(st.events),
        "recent_events": [
            {"date": e.date, "type": e.type, "close": e.price,
             "broken_pivot": e.broken_pivot}
            for e in st.events[-max_events:]],
        "last_sh": None if st.last_sh is None else
            {"date": dates[st.last_sh.idx], "price": st.last_sh.price,
             "consumed": st.last_sh.consumed},
        "last_sl": None if st.last_sl is None else
            {"date": dates[st.last_sl.idx], "price": st.last_sl.price,
             "consumed": st.last_sl.consumed},
        "order_block": ob,
        "note": f"摆动点确认滞后 {k} 根；订单块为突破前逆向末根 K 线的近似参考区",
    }
