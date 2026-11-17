# -*- coding: utf-8 -*-
"""全球指数免费源（腾讯行情）：美股三大指数 + 港股恒生系（2026-11-07）。

起因：用户"暂时只需要指数"，而 TickFlow 在线行情**不提供指数及指数 ETF**
（实测 /v1/instruments 查 SPX.US/HSI.HK 均空、港股无指数 ETF）。用户拍板
"指数 TickFlow 没有就都走腾讯之类的免费数据源"——本模块即该免费通道，
不占 TickFlow 的 10/min 额度。

实测可用代码（qt.gtimg.cn，GBK 解码）：
- usDJI 道琼斯 / usIXIC 纳斯达克 / usINX 标普500
- hkHSI 恒生指数 / hkHSTECH 恒生科技 / hkHSCEI 国企指数
快照字段与 A 股一致：[1]名 [3]价 [4]昨收 [5]开 [30]时间 [31]涨跌额
[32]涨跌幅%(已是百分数) [33]高 [34]低。
日K：web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<code>,day,,,N,qfq。
"""
from __future__ import annotations

from datetime import datetime

import httpx

# 前端/路由使用的统一指数代码 → 腾讯代码
INDEX_MAP: dict[str, str] = {
    # A 股核心指数（看板指数条点击看实时行情，2026-11-07）
    "000001.SH": "sh000001",     # 上证指数
    "399001.SZ": "sz399001",     # 深证成指
    "399006.SZ": "sz399006",     # 创业板指
    "000680.SH": "sh000680",     # 科创综指
    # 美股 / 港股
    "DJI.US": "usDJI",
    "IXIC.US": "usIXIC",
    "INX.US": "usINX",
    "HSI.HK": "hkHSI",
    "HSTECH.HK": "hkHSTECH",
    "HSCEI.HK": "hkHSCEI",
}

_QUOTE_URL = "https://qt.gtimg.cn/q="
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
# 美股指数 fqkline 只回最近 1 根；备用日K接口可回 120 根（2026-11-11 实测）
_KLINE_FALLBACK_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/kline"
# 分时双入口（2026-11-11）：腾讯网关把 /appstock 老路由迁到 /ifzqgtimg
# 前缀（老路径 404、web.ifzq 反爬 501）——现行路由置顶，老入口降级。
_MINUTE_URLS = [
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query",
    "https://web.ifzq.gtimg.cn/appstock/app/minute/query",
]
_UA = {"User-Agent": "Mozilla/5.0 (astock-hub index probe)"}


class GlobalIndexError(RuntimeError):
    """腾讯全球指数取数失败（网络/空数据），消息面向用户。"""


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quotes(symbols: list[str], timeout_s: float = 15.0) -> list[dict]:
    """指数快照。symbols 用 INDEX_MAP 的键（如 DJI.US、HSI.HK）。"""
    unknown = [s for s in symbols if s not in INDEX_MAP]
    if unknown:
        raise GlobalIndexError(f"不支持的指数代码：{','.join(unknown)}")
    codes = [INDEX_MAP[s] for s in symbols]
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0)) as client:
            resp = client.get(_QUOTE_URL + ",".join(codes), headers=_UA)
    except httpx.HTTPError as e:
        raise GlobalIndexError(f"腾讯行情请求失败：{e}")
    text = resp.content.decode("gbk", errors="replace")
    items: list[dict] = []
    for sym, code in zip(symbols, codes):
        marker = f'v_{code}="'
        start = text.find(marker)
        if start < 0:
            continue
        body = text[start + len(marker):].split('"', 1)[0]
        f = body.split("~")
        if len(f) < 35:
            continue
        ts_raw = f[30].strip()
        ts_ms = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                ts_ms = int(datetime.strptime(ts_raw, fmt).timestamp() * 1000)
                break
            except ValueError:
                continue
        # 量额（用户 2026-11-08 要求显示）：A 股量=f[6]（手）、额=f[37]（万元→元）；
        # 港股 f[6]/f[37] 同为成交额（万港元→港元），无"手"口径，量不硬填；
        # 美股量=f[6]（股），额字段口径不可靠不硬填。
        if sym.endswith((".SH", ".SZ")):
            volume = _f(f[6])
            amount = _f(f[37]) * 1e4 if _f(f[37]) is not None else None
        elif sym.endswith(".HK"):
            volume = None
            amount = _f(f[37]) * 1e4 if _f(f[37]) is not None else None
        else:
            volume = _f(f[6])
            amount = None
        items.append({
            "symbol": sym,
            "name": f[1] or code,
            "region": "US" if sym.endswith(".US") else ("HK" if sym.endswith(".HK") else "CN"),
            "price": _f(f[3]),
            "prev_close": _f(f[4]),
            "change_pct": _f(f[32]),          # 腾讯已是百分数，不再换算
            "open": _f(f[5]),
            "high": _f(f[33]),
            "low": _f(f[34]),
            "volume": volume, "amount": amount,
            "timestamp": ts_ms,
        })
    if not items:
        raise GlobalIndexError("腾讯指数快照为空（代码可能失效或上游异常）")
    return items


def kline(symbol: str, count: int = 120, timeout_s: float = 15.0) -> list[dict]:
    """指数日K（qfqday/day 两种键兼容），行式 OHLCV。

    fqkline 对美股指数只回最近一根（2026-11-11 实测），拿到少于 2 根时
    自动切备用日K接口；备用返回键可能是 us.DJI，解析时按数据内容识别。
    """
    code = INDEX_MAP.get(symbol)
    if not code:
        raise GlobalIndexError(f"不支持的指数代码：{symbol}")
    attempts = (
        (_KLINE_URL, f"{code},day,,,{count},qfq"),
        (_KLINE_FALLBACK_URL, f"{code},day,,,{count}"),
    )
    last_rows: list[dict] | None = None
    last_error: Exception | None = None
    for url, param in attempts:
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0)) as client:
                resp = client.get(url, params={"param": param}, headers=_UA)
            data = resp.json().get("data") or {}
            node = data.get(code)
            if not isinstance(node, dict):
                node = next((v for v in data.values()
                             if isinstance(v, dict)
                             and (v.get("qfqday") or v.get("day"))), None)
            if not isinstance(node, dict):
                raise KeyError(code)
            raw = node.get("qfqday") or node.get("day") or []
            rows = []
            for r in raw:
                rows.append({
                    "date": r[0],
                    "open": _f(r[1]), "close": _f(r[2]),
                    "high": _f(r[3]), "low": _f(r[4]),
                    "volume": _f(r[5]) if len(r) > 5 else None,
                    "amount": None,
                })
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            last_error = e
            continue
        if len(rows) >= 2:
            return rows
        last_rows = rows
    if last_rows is not None:
        return last_rows
    if isinstance(last_error, httpx.HTTPError):
        raise GlobalIndexError(f"腾讯指数K线请求失败：{last_error}")
    raise GlobalIndexError("腾讯指数K线返回结构异常")


__all__ = ["INDEX_MAP", "GlobalIndexError", "quotes", "kline", "intraday"]


def intraday(symbol: str, timeout_s: float = 15.0) -> dict:
    """指数当日分时（腾讯 minute/query 双入口轮换）。返回 {date, points:[{t,price,volume}]}。

    实测：sh/sz A 股指数与 hkHSI 有全量分钟线；usDJI 等美股指数只回当前点
    （date 空、1 条）——属上游不支持，返回空 points 让前端如实提示，不硬画。
    """
    code = INDEX_MAP.get(symbol)
    if not code:
        raise GlobalIndexError(f"不支持的指数代码：{symbol}")
    last_err = None
    for url in _MINUTE_URLS:
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0)) as client:
                resp = client.get(url, params={"code": code}, headers=_UA)
            node = resp.json()["data"][code]
            data = node.get("data") or {}
            raw = data.get("data") or []
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            last_err = e  # 入口失败/反爬 HTML/结构异常 → 落下一入口
            continue
        points = []
        for line in raw:
            parts = str(line).split()
            if len(parts) < 2 or len(parts[0]) != 4:
                continue
            t = parts[0]
            points.append({
                "t": f"{t[:2]}:{t[2:]}",
                "price": _f(parts[1]),
                "volume": _f(parts[2]) if len(parts) > 2 else None,
            })
        if len(points) < 5:  # 只有当前点的市场（美股）按无分时处理
            points = []
        return {"date": data.get("date") or None, "points": points}
    raise GlobalIndexError(f"腾讯指数分时所有入口均失败：{last_err}")

