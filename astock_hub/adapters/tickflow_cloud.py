# -*- coding: utf-8 -*-
"""TickFlow 在线行情 API（docs.tickflow.org）付费源接入（2026-11-07）。

**与已弃用的本地 tickflow 项目不是一回事**——这是
tickflow.org 的在线数据服务，HTTP + `x-api-key` 头鉴权，官方文档见
docs.tickflow.org/zh-Hans。接入只做「数据源页」的凭证管理与连通性探测，
不抢行情路由（axdata 主源不变）。

官方口径（2026-11-07 实测 OpenAPI /zh-Hans/api-reference/openapi.json）：
- 完整服务：https://api.tickflow.org（需 API Key，控制台一键生成）
- 免费服务：https://free-api.tickflow.org（无需 Key，仅历史日K）
- 端点均为 /v1/...；最轻的 `/v1/exchanges`（交易所列表）用于探测；
  错误/无权限 Key 返回 401，超频返回 429。
"""
from __future__ import annotations

import threading
import time

import httpx

_DEFAULT_BASE = "https://api.tickflow.org"

# 实测口径（2026-11-07 真机）：/v1/quotes 单次最多 5 个标的
# （TOO_MANY_SYMBOLS: 12 (最大: 5)）；全账号限流 10 次/分钟
# （RATE_LIMITED 请求频率超限 (10/min)）。 outbound 串行限速 + 分批，
# 避免看板轮询/弹窗/探测叠加把额度打爆。
_MAX_SYMBOLS_PER_REQUEST = 5
_MIN_INTERVAL_S = 6.5          # 10/min → 每次至少间隔 6.5s
_THROTTLE_LOCK = threading.Lock()
_last_call_at = 0.0


def _throttle() -> None:
    """进程内串行限速：相邻两次 outbound 调用至少隔 _MIN_INTERVAL_S。"""
    global _last_call_at
    with _THROTTLE_LOCK:
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def check_key_format(api_key: str) -> tuple[bool, str]:
    """Key 格式关：非空即可（官方未公布固定前缀，长度下限仅挡明显误填）。"""
    key = (api_key or "").strip()
    if not key:
        return False, "Key 为空（在 tickflow.org 控制台一键生成）"
    if len(key) < 8:
        return False, "Key 过短，请核对控制台复制的完整 API Key"
    return True, "ok"


def probe(api_key: str, api_base_url: str | None = None,
          timeout_s: float = 20.0) -> tuple[bool, str]:
    """最小实拉探测：GET /v1/exchanges（交易所列表）。

    返回 (ok, 中文结论)。401/403=Key 无效，429=Key 有效但限流（如实区分），
    网络错误不冒充 Key 问题。
    """
    ok, msg = check_key_format(api_key)
    if not ok:
        return False, msg
    base = (api_base_url or _DEFAULT_BASE).strip().rstrip("/")
    _throttle()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=6.0)) as client:
            resp = client.get(f"{base}/v1/exchanges",
                              headers={"x-api-key": api_key.strip()})
    except httpx.TimeoutException:
        return False, f"连接超时（{timeout_s:.0f}s）——检查网络或接口地址后重试"
    except httpx.HTTPError as e:
        return False, f"网络请求失败：{e}"

    if resp.status_code in (401, 403):
        return False, "Key 无效或无权限（401/403）——到 tickflow.org 控制台核对 API Key"
    if resp.status_code == 429:
        return True, "Key 有效，但触发限流（429）——稍后再试或升级套餐"
    if resp.status_code != 200:
        return False, f"服务返回 HTTP {resp.status_code}：{resp.text[:150]}"
    try:
        payload = resp.json()
    except ValueError:
        return False, "返回不是 JSON（可能接口地址填错或代理劫持）"
    data = payload.get("data") if isinstance(payload, dict) else payload
    n = len(data) if isinstance(data, list) else 0
    return True, f"连接正常：交易所列表返回 {n} 条，Key 有效"


__all__ = ["check_key_format", "probe"]


# ---------------------------------------------------------------- 取数（美股/港股/其它市场）

class TickflowError(RuntimeError):
    """TickFlow 取数失败（网络/鉴权/限流/参数），消息面向用户。"""


def _request(api_key: str, path: str, params: dict,
             base_url: str | None, timeout_s: float = 20.0) -> dict:
    if not (api_key or "").strip():
        raise TickflowError("未配置 TickFlow Key——请先在「数据源」页填写你的 API Key")
    base = (base_url or _DEFAULT_BASE).strip().rstrip("/")
    _throttle()  # 限流 10/min：所有 outbound 先排队再发
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=6.0)) as client:
            resp = client.get(base + path, params=params,
                              headers={"x-api-key": api_key.strip()})
    except httpx.TimeoutException:
        raise TickflowError(f"连接超时（{timeout_s:.0f}s），稍后重试")
    except httpx.HTTPError as e:
        raise TickflowError(f"网络请求失败：{e}")
    if resp.status_code in (401, 403):
        raise TickflowError("TickFlow Key 无效或无权限（401/403），到控制台核对")
    if resp.status_code == 429:
        raise TickflowError("TickFlow 限流（429），稍后再试或升级套餐")
    if resp.status_code != 200:
        # 透出上游业务错误（如 TOO_MANY_SYMBOLS 最大 5 只），别只报状态码
        detail = ""
        try:
            payload = resp.json()
            detail = payload.get("message") or payload.get("msg") or ""
        except ValueError:
            detail = resp.text[:120]
        raise TickflowError(
            f"TickFlow 返回 HTTP {resp.status_code}" + (f"：{detail}" if detail else ""))
    try:
        return resp.json()
    except ValueError:
        raise TickflowError("TickFlow 返回不是 JSON（可能接口地址填错）")


def quotes(api_key: str, symbols: list[str], base_url: str | None = None) -> list[dict]:
    """实时快照（支持 A股/美股/港股，如 AAPL.US、00700.HK）。

    单次最多 5 个标的（TOO_MANY_SYMBOLS 实测），>5 自动分批后合并；
    输出统一为百分数涨跌幅（源 change_pct 是小数：0.01 → 1%）。
    """
    payload_data: list[dict] = []
    for i in range(0, len(symbols), _MAX_SYMBOLS_PER_REQUEST):
        chunk = symbols[i:i + _MAX_SYMBOLS_PER_REQUEST]
        payload = _request(api_key, "/v1/quotes",
                           {"symbols": ",".join(chunk)}, base_url)
        payload_data.extend(payload.get("data") or [])
    items = []
    for q in payload_data:
        ext = q.get("ext") or {}
        cp = ext.get("change_pct")
        price = q.get("last_price")
        prev = q.get("prev_close")
        if cp is None and price is not None and prev:
            cp = (price - prev) / prev
        items.append({
            "symbol": q.get("symbol"),
            "name": ext.get("name"),
            "region": q.get("region"),
            "price": price,
            "prev_close": prev,
            "change_pct": cp * 100 if cp is not None else None,
            "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
            "volume": q.get("volume"), "amount": q.get("amount"),
            "timestamp": q.get("timestamp"),
        })
    return items


def kline(api_key: str, symbol: str, period: str = "1d", count: int = 120,
          base_url: str | None = None) -> list[dict]:
    """日K/分钟K（列式 CompactKlineData → 行式）。"""
    payload = _request(api_key, "/v1/klines",
                       {"symbol": symbol, "period": period, "count": count}, base_url)
    d = payload.get("data") or {}
    ts = d.get("timestamp") or []
    opens, highs = d.get("open") or [], d.get("high") or []
    lows, closes = d.get("low") or [], d.get("close") or []
    vols, amts = d.get("volume") or [], d.get("amount") or []
    from datetime import datetime
    rows = []
    for i, t in enumerate(ts):
        # 时间戳毫秒 → 日期/时分（交易所当地口径，展示用）
        dt = datetime.fromtimestamp(t / 1000)
        rows.append({
            "date": dt.strftime("%Y-%m-%d") if period in ("1d", "1w", "1M", "1Q", "1Y")
                    else dt.strftime("%Y-%m-%d %H:%M"),
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": closes[i] if i < len(closes) else None,
            "volume": vols[i] if i < len(vols) else None,
            "amount": amts[i] if i < len(amts) else None,
        })
    return rows

