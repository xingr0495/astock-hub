"""A 股交易时段判定（北京工作日 9:30-11:30 / 13:00-15:00）。

从 webapi /api/quote 的内联逻辑抽出，供看板与行情接口复用。now 可注入便于测试。
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

_MORNING_START = dtime(9, 30)
_MORNING_END = dtime(11, 30)
_AFTERNOON_START = dtime(13, 0)
_AFTERNOON_END = dtime(15, 00)


def market_session(now: datetime | None = None) -> tuple[str, bool]:
    """返回 (session, is_trading)。

    session：pre（盘前）/ open（盘中，含集合竞价后）/ post（盘后及午间休市）/ closed。
    """
    now = now or datetime.now()
    t = now.time()
    weekday = now.weekday() < 5
    is_session = weekday and (
        _MORNING_START <= t <= _MORNING_END
        or _AFTERNOON_START <= t <= _AFTERNOON_END)

    if t < _MORNING_START:
        session = "pre"
    elif is_session:
        session = "open"
    elif t < _AFTERNOON_END:
        session = "post"
    else:
        session = "closed"
    return session, bool(is_session)


def session_progress(now: datetime | None = None) -> float | None:
    """盘中返回已交易时长占全天 240 分钟比例（供盘中量比线性外推）；非盘中 None。"""
    now = now or datetime.now()
    t = now.time()
    if now.weekday() >= 5:
        return None
    if _MORNING_START <= t <= _MORNING_END:
        elapsed = (datetime.combine(now.date(), t)
                   - datetime.combine(now.date(), _MORNING_START))
    elif _AFTERNOON_START <= t <= _AFTERNOON_END:
        elapsed = (timedelta(hours=2)
                   + datetime.combine(now.date(), t)
                   - datetime.combine(now.date(), _AFTERNOON_START))
    else:
        return None
    return max(0.0, min(1.0, elapsed.total_seconds() / 240 / 60))


# 盘中量比折算相关函数（与 tickflow 对齐）

_TRADING_TOTAL_MINUTES = 240  # 9:30-11:30(120分) + 13:00-15:00(120分)


def trading_minutes_elapsed(now: datetime | None = None) -> float:
    """计算从开盘到当前的累计交易分钟数（0-240）。

    盘中：返回实际累计分钟数
    盘后：返回 240（全天）
    盘前：返回 0
    非交易日：返回 240（视作全天）
    """
    now = now or datetime.now()
    t = now.time()
    weekday = now.weekday() < 5

    if not weekday:
        return float(_TRADING_TOTAL_MINUTES)

    if t < _MORNING_START:
        return 0.0
    elif t <= _MORNING_END:
        return (t.hour * 60 + t.minute - 9 * 60 - 30) + t.second / 60.0
    elif t < _AFTERNOON_START:
        return 120.0
    elif t <= _AFTERNOON_END:
        return 120.0 + (t.hour * 60 + t.minute - 13 * 60) + t.second / 60.0
    else:
        return float(_TRADING_TOTAL_MINUTES)


def time_factor_for_vol_ratio(now: datetime | None = None) -> float:
    """计算量比的时间因子：240 / elapsed_minutes。

    盘中：time_factor > 1（部分量折算成全天量级）
    盘后：time_factor = 1（不折算）
    盘前：time_factor = 1（不折算）
    """
    elapsed = trading_minutes_elapsed(now)
    if elapsed <= 0:
        return 1.0
    return 240.0 / elapsed
