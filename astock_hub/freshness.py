"""数据新鲜度判定。

核心规则：as_of 永远来自数据本身的时间戳；陈旧（stale）只是标注，不是错误——
季报型数据（国家队持仓）披露间隔本来就长，陈旧属正常现象。
"""

from __future__ import annotations

import re
from datetime import date, datetime

# 各类数据的容忍度（单位：交易日，近似——不维护节假日表，只跳过周末）
KIND_TTL = {
    "realtime": 1,
    "daily": 2,
    "weekly": 8,  # 周采样数据（ETF 份额，通常周五出）
    "quarterly": 120,  # 季报型（股东持仓按财报披露，间隔可达 4 个月）
}

KIND_NOTE = {
    "quarterly": "季报型数据：按财报期披露，距上次披露时间久属正常现象",
}


def parse_as_of(raw) -> date | None:
    """把各种格式的时间字符串解析成日期。支持 2026-09-17 / 20260917 / 带时分秒 / ISO。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:26], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T", text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def trading_age_days(d: date, today: date) -> int:
    """从 d 到 today 的近似交易日数（只跳周末，不查节假日表）。"""
    if d >= today:
        return 0
    days = 0
    cur = d
    while cur < today:
        cur = date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            days += 1
    return days


def assess(as_of_raw, kind: str = "daily", today: date | None = None) -> dict:
    """评估新鲜度，返回 Envelope 里 meta.freshness 的内容。"""
    today = today or date.today()
    ttl = KIND_TTL.get(kind, KIND_TTL["daily"])
    d = parse_as_of(as_of_raw)
    result: dict = {"ttl_days": ttl, "trading_day_approx": True}
    if kind in KIND_NOTE:
        result["note"] = KIND_NOTE[kind]
    if d is None:
        result.update({"age_days": None, "stale": None, "note": "无法解析数据时间，新鲜度未知"})
        return result
    age = trading_age_days(d, today)
    result["age_days"] = age
    result["stale"] = age > ttl
    if kind == "quarterly" and result["stale"]:
        result["note"] = KIND_NOTE["quarterly"] + "；但已超过约两个披露周期，建议先刷新数据"
    return result
