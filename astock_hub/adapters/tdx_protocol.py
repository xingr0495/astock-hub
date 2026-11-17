# -*- coding: utf-8 -*-
"""通达信协议当日全市场快照（axdata stock_realtime_rank_tdx，阶段三）。

实测锚点（2026-10-15）：单页恒 80 行（count 客户端上限 1000）、全市场 5581 只
（SH 2320/SZ 2908/BJ 353）、74 页约 6 分钟；rank **无日期参数**只返回当前快照；
volume 单位 = **手**（amount/volume/100≈均价），入库前 ×100 转股以兼容
parquet_store 既有的 /100（股→手）逻辑。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

import pandas as pd
import polars as pl

logger = logging.getLogger(__name__)

PAGE_STRIDE = 80       # 实测服务端每页恒 80 行，步进必须 80
MAX_PAGES = 90         # 74 页实测 + 余量（防死循环）
MIN_ROWS = 4500        # A 股（沪深北）行数下限（zip 侧 TDX_MIN_PRICED 同思路）
_PAGE_RETRY, _PAGE_SLEEP = 2, 5.0


def _rank_page(start: int) -> pd.DataFrame:
    """拉一页（AxDataClient 实现细节集中在测试 monkeypatch 点）。"""
    from axdata import AxDataClient
    df = AxDataClient().call("stock_realtime_rank_tdx", start=start, count=1000)
    return pd.DataFrame() if df is None else df


def protocol_eligible(day_str: str, now: datetime, cfg: dict) -> bool:
    """协议腿资格：当日 ∧ 已收盘(≥15:31，避开 15:05-15:30 盘后定价交易，
    a-stock-data SKILL §1.4 实测) ∧ 工作日 ∧ 在交易日历。

    盘中=半日 bar 禁入库；15:31 前=结算/盘后定价窗口（OHLC 已定但
    volume/amount 偏小，写库后 last_date 推进永不重取）；假日=防伪造当日行
    （zip 的 404 天然防护在协议侧不存在，全部在此显式补上）。
    """
    if day_str != now.strftime("%Y-%m-%d"):
        return False
    if now.weekday() >= 5:
        return False
    if (now.hour, now.minute) < (15, 31):
        return False
    return day_str in _calendar_days(cfg)


def _calendar_days(cfg: dict) -> set[str]:
    from ..store.market_data import calendar_days
    try:
        return {str(d)[:10] for d in calendar_days(cfg)}
    except Exception as e:  # noqa: BLE001 — 日历取不到宁可不走协议（fail-safe 走 zip）
        logger.warning("protocol_eligible 日历不可用: %s", e)
        return set()


def tdx_full_market_day(day_str: str) -> pl.DataFrame:
    """当前交易日全市场日线快照 → zip 同构行（volume 已转股）。

    分页步进 80、单页重试 2 次（5s）、按 instrument_id 去重、
    非有限值/非正收盘丢弃、停牌 volume=0 保留、总行数 <4500 抛 ValueError。
    当日自守卫：rank 无日期参数只返回当前快照，贴标日≠系统日直接拒绝。
    """
    if day_str != date.today().isoformat():
        raise ValueError("协议快照只能取当前交易日")
    seen: set[str] = set()
    rows: list[dict] = []
    start, empty, stall = 0, 0, 0
    pages = 0
    while pages < MAX_PAGES and empty < 2 and stall < 3:
        df = None
        for attempt in range(_PAGE_RETRY + 1):
            try:
                df = _rank_page(start)
                break
            except Exception:  # noqa: BLE001 — 单页网络抖动
                if attempt == _PAGE_RETRY:
                    raise
                time.sleep(_PAGE_SLEEP)
        pages += 1
        if df is None or len(df) == 0:
            empty += 1
            start += PAGE_STRIDE
            continue
        fresh = 0
        for rec in df.to_dict("records"):
            sym = str(rec.get("instrument_id") or "").strip()
            if not sym or sym in seen:
                continue
            try:
                o = float(rec["open"]); h = float(rec["high"])
                l = float(rec["low"]); c = float(rec["last_price"])
                v = float(rec["volume"]); a = float(rec["amount"])
            except (KeyError, TypeError, ValueError):
                continue
            if not all(map(_finite, (o, h, l, c, v, a))) or c <= 0:
                continue
            seen.add(sym)
            fresh += 1
            rows.append({"thscode": sym, "date": day_str,
                         "open": round(o, 4), "high": round(h, 4),
                         "low": round(l, 4), "close": round(c, 4),
                         "volume": int(v * 100),   # 手→股（×100，与 zip 同口径）
                         "amount": round(a, 2)})
        if fresh == 0:
            stall += 1
        else:
            stall = 0
        empty = 0   # 非空页复位瞬空计数（连续语义，与 stall 复位对齐）
        start += PAGE_STRIDE
    if len(rows) < MIN_ROWS:
        raise ValueError(f"通达信协议全市场行数不足：{len(rows)} < {MIN_ROWS}（{pages} 页）")
    logger.info("协议全市场 %s 页 %s 行", pages, len(rows))
    # seen 集合已按 instrument_id 去重；直接构造保列序 thscode,date,open,...,amount
    return pl.DataFrame(rows)


def _finite(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))
