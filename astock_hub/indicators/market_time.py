"""A 股市场时间工具（从 tickflow `backend/app/market_time.py` 移植，只取当前需要的部分）。

固定北京时间（UTC+8，无夏令时）：服务器/容器本地时区不可靠，凡是与"今天是哪个
交易日"相关的判断都必须显式用北京时间，否则跨时区部署时日期会整体错开一天。

原文件里的 `trading_minutes_elapsed*`（盘中量比时间折算）未移植——astock-hub 走的是
盘后完整数据，量比不需要按已交易分钟数折算；将来要做盘中实时折算时再补。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def cn_now() -> datetime:
    """当前北京时间（带时区）。"""
    return datetime.now(CN_TZ)


def cn_today() -> date:
    """当前北京日期。"""
    return datetime.now(CN_TZ).date()
