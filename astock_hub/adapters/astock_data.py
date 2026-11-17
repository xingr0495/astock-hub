"""a-stock-data 适配器 —— 集成腾讯K线和通达信盘后包等数据源。

直接从 a-stock-data (simonlin1212/a-stock-data) 移植关键函数，
避免执行整个 SKILL.md（太慢）。
"""

from __future__ import annotations

import io
import math
import re
import struct
import time
import zipfile
from datetime import datetime, date, timedelta, timezone
from datetime import time as dt_time

import httpx
import polars as pl

from .. import models
from .base import Adapter, timed


# ============================================================
# a-stock-data 辅助函数（从 SKILL.md 移植）
# ============================================================

# 东财 push2 板块资金流熔断器：部分住宅 IP 会被 push2 连接级风控（HTTP 000
# 秒拒，a-stock-data CHANGELOG #18 有记录），2026-10-09 实测触发。面板盘中
# 每 2 分钟轮询会把间歇封禁"续杯"，故连续 2 轮全失败后熔断冷却（30min→1h→
# 2h 封顶），冷却期直接返回 unavailable，不再打扰东财。
_EM_MIN_INTERVAL = 1.0          # 全局东财请求最小间隔（秒），对齐 a-stock-data 标准
_EM_LAST_CALL = [0.0]
_BOARD_FF_COOLDOWNS = [30 * 60, 60 * 60, 120 * 60]   # 沿用原列表
# 熔断按「主机+路径」记账：push2/clist 与 push2/fflow 互不连坐（2026-10-14 实测两路径一通一拒）
_BOARD_FF_BREAKERS: dict[str, dict] = {}

_CLIST_KEY = "push2/api/qt/clist/get"


def _breaker(key: str) -> dict:
    return _BOARD_FF_BREAKERS.setdefault(
        key, {"consec_fail": 0, "open_until": 0.0})


def _alert_source_risk(key: str, kind: str, detail: str) -> None:
    """数据源风控 → 飞书提醒（11-07 拍板"某个数据源风控用飞书提醒"）。

    kind：fail=开闸告警 / recovered=试通恢复知会（chain_guard 防重键
    按 kind 分域，同日各最多一条）。铁律：告警自身失败绝不影响取数。
    """
    try:
        from datetime import date as _d
        from socket import gethostname
        from ..chain_guard import send_alert
        if kind == "fail":
            body = (f"{detail}\n"
                    f"影响：板块资金流·东财备胎路径显示不可用（主源开盘红"
                    f"不受影响）；冷却结束自动试通，恢复会再知会一条。")
        else:
            body = "熔断已试通恢复，数据源可用。"
        content = (f"**级别**：{'警告' if kind == 'fail' else '恢复'}\n"
                   f"**来源**：数据源风控（{key}）\n"
                   f"主机：{gethostname()}\n"
                   f"{body}")
        send_alert(_d.today().isoformat(), "data-src", content, kind=kind)
    except Exception:  # noqa: BLE001 — 铁律：告警失败不影响取数
        pass


def _em_throttle() -> None:
    import random
    wait = _EM_MIN_INTERVAL - (time.monotonic() - _EM_LAST_CALL[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    _EM_LAST_CALL[0] = time.monotonic()


def _v39_num(s):
    """安全转换为 float，失败返回 None"""
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _v39_req_num(s, name):
    """必须转换为 float，失败抛异常"""
    v = _v39_num(s)
    if v is None:
        raise ValueError(f"{name} 不能为空")
    return v


def _v39_src_date(s):
    """日期字符串 → date 对象"""
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _v39_date(s):
    """日期字符串标准化"""
    return s[:10] if s else ""


def norm_ticker(code: str, stock_only: bool = False) -> str:
    """代码格式标准化 → 纯 6 位数字。

    `stock_only` 仅为调用方语义标记（指数/基金也传同样的 6 位数字），
    目前不影响输出，保留参数是为了兼容 a-stock-data 原函数的签名。
    """
    import re
    code = str(code).strip().upper()
    for prefix in ["SH", "SZ", "BJ"]:
        code = code.replace(prefix, "")
    code = code.split(".")[0]
    m = re.search(r"\d{6}", code)
    if m:
        return m.group(0)
    return code


def get_prefix(code: str) -> str:
    """判断代码市场前缀"""
    code = str(code).upper()
    if code.startswith("SH") or code.endswith(".SH"):
        return "sh"
    if code.startswith("SZ") or code.endswith(".SZ"):
        return "sz"
    if code.startswith("BJ") or code.endswith(".BJ"):
        return "bj"
    pure = norm_ticker(code)
    # 北交所代码段必须排在 ("6","9") 沪市判断之前：920 以 "9" 开头会被
    # 提前命中，原 ("920",) 分支不可达（2026-10-14 任务审查发现）；
    # 43/83/87 此前则会误落到 sz。
    if pure.startswith(("43", "83", "87", "920")):
        return "bj"
    if pure.startswith(("6", "9")):
        return "sh"
    return "sz"


# ============================================================
# 腾讯K线
# ============================================================

# 2026-11-11：腾讯财经网关把 /appstock 老路由迁到 /ifzqgtimg 前缀下
# （老路径 404 Route Not Found、web.ifzq 反爬 501——11-10~11 模拟盘引擎
# 分时双入口全死、静默盲跑两天"该卖没卖"的事故），新前缀置顶为主入口，
# 老入口保留降级。分钟线/fqkline/手抄件 K 线全走本列表轮换。
_TENCENT_HOSTS = [
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com",
]
_TENCENT_SPAN_DAYS = {"day": 640, "week": 640 * 7, "month": 640 * 30}


# ============================================================
# axdata 统一代码路由（阶段二 2A）
# ============================================================

# 腾讯快照裸代码的沪市指数段（000300 等以 0 开头，按数字前缀会误落 sz）
_SH_INDEX = {"000300", "000905", "000016", "000688", "000852", "000010"}


def _axdata_code(code: str) -> str:
    """hub 代码口径（裸 6 位 / sz000001 / 000001.SZ）→ axdata 统一代码 000001.SZ。

    路由沿用 tencent_quote 的前缀惯例：92 先于 9 判北交所（920 以 9 开头
    会被沪市分支抢先）、4/8 北交所、裸指数段归沪市——与 get_prefix 同源
    （2026-10-14 修复口径），差异仅在裸指数代码。
    """
    text = str(code).strip()
    upper = text.upper()
    if upper.startswith(("SH", "SZ", "BJ")):
        return f"{norm_ticker(upper)}.{upper[:2]}"
    if upper.endswith((".SH", ".SZ", ".BJ")):
        return upper
    bare = norm_ticker(upper)
    if bare.startswith("92") or bare.startswith(("4", "8")):
        ex = "BJ"
    elif bare in _SH_INDEX or bare.startswith(("5", "6", "9")):
        ex = "SH"
    else:
        ex = "SZ"
    return f"{bare}.{ex}"


def _axdata_num(v):
    """axdata/pandas 数值 → float | None（NaN/inf → None，与 _v39_num 同语义）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _tencent_kline_call(path, params):
    """腾讯 K 线 API 调用（轮换入口）"""
    last_err = None
    for host in _TENCENT_HOSTS:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{host}{path}", params={"param": params})
                resp.raise_for_status()
                data = resp.json().get("data", {})
                if data:
                    return data, host
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"腾讯 K 线所有入口均失败: {last_err}")


def _tencent_kline_legacy(code, period="day", adjust=None, start=None, end=None, count=320):
    """腾讯K线手抄件（多入口轮换 + 日期窗切块）——阶段二 2A 起为降级腿。"""
    import polars as pl
    from datetime import datetime, timedelta

    period = str(period).lower()
    symbol = get_prefix(code) + norm_ticker(code)
    adjust = "qfq" if adjust is None else adjust

    if start:
        first = datetime.strptime(_v39_date(start), "%Y-%m-%d").date()
        last = datetime.strptime(_v39_date(end), "%Y-%m-%d").date() if end else date.today()
        windows = []
        cursor = first
        while cursor <= last:
            stop = min(cursor + timedelta(days=_TENCENT_SPAN_DAYS.get(period, 640) - 1), last)
            windows.append((cursor.isoformat(), stop.isoformat(), 640))
            cursor = stop + timedelta(days=1)
    else:
        if not 1 <= int(count) <= 640:
            count = 320
        windows = [("", "", int(count))]

    by_date = {}
    for s, e, n in windows:
        data, host = _tencent_kline_call(
            "/appstock/app/fqkline/get",
            f"{symbol},{period},{s},{e},{n},{adjust}")
        node = data.get(symbol)
        key = adjust + period
        items = node.get(key, node.get(period)) if node else []
        if items:
            for item in items:
                day = _v39_src_date(item[0])
                if day in by_date:
                    continue
                by_date[day] = {
                    "date": day.isoformat(),
                    "open": _v39_req_num(item[1], "open"),
                    "high": _v39_req_num(item[3], "high"),
                    "low": _v39_req_num(item[4], "low"),
                    "close": _v39_req_num(item[2], "close"),
                    "volume": _v39_req_num(item[5], "volume"),
                }

    rows = [by_date[k] for k in sorted(by_date)]
    df = pl.DataFrame(rows).with_columns(pl.lit(symbol).alias("code"))
    return df


_KLINE_DTYPES = {"date": pl.Utf8, "open": pl.Float64, "high": pl.Float64,
                 "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
                 "code": pl.Utf8}


def _axdata_tencent_kline(code, adjust=None, start=None, end=None, count=320):
    """axdata 腾讯源日K（stock_zh_a_hist_tx）→ tencent_kline 同构帧。

    口径（2026-11-01 灰度实测）：volume 已是**手**（与旧实现同，不换算）；
    date 统一 YYYY-MM-DD；`code` 列 = get_prefix+6位（与旧实现同）。
    axdata 语义差异两处，包装层对齐：
    - `end_date` 缺省 = start_date（单日），「最近 N 根」必须显式给止日；
    - `limit` 取区间**最早** 640 根（升序截头），「最近 N 根」在本地 tail；
      起点自算 = 今日 - (count×7/5 + 25) 日历日（交易日≈日历×5/7，缓冲
      覆盖长假），确保区间行数 ≥ count 后 tail 结果正确。
    给定 start/end 的长区间按年切块（单块 >640 根会被 axdata 截头丢新）。
    """
    from datetime import datetime, timedelta

    symbol = get_prefix(code) + norm_ticker(code)
    instrument = _axdata_code(code)
    adj = "qfq" if adjust is None else adjust
    ax_adj = adj if adj in ("qfq", "hfq") else "none"
    today = date.today()

    if start:
        start_date = _v39_date(start).replace("-", "")
        last = datetime.strptime(_v39_date(end), "%Y-%m-%d").date() if end else today
        end_date = last.strftime("%Y%m%d")
        if start_date > end_date:
            raise ValueError(f"start {start_date} 晚于 end {end_date}")
        windows = []
        for y in range(int(start_date[:4]), int(end_date[:4]) + 1):
            windows.append((max(start_date, f"{y}0101"),
                            min(end_date, f"{y}1231")))
        trim_tail = False
    else:
        n = int(count)
        if not 1 <= n <= 640:
            n = 320
        end_date = today.strftime("%Y%m%d")
        start_date = (today - timedelta(days=n * 7 // 5 + 25)).strftime("%Y%m%d")
        windows = [(start_date, end_date)]
        trim_tail = True

    client = _axdata_client()
    by_date = {}
    for w_start, w_end in windows:
        pdf = client.call("stock_zh_a_hist_tx", code=instrument, adjust=ax_adj,
                          start_date=w_start, end_date=w_end, limit=640)
        if pdf is None or len(pdf) == 0:
            continue
        for row in pdf.to_dict("records"):
            d = str(row.get("trade_date") or "")
            if len(d) != 8 or not d.isdigit():
                continue
            day = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            if day in by_date:
                continue
            by_date[day] = {
                "date": day,
                "open": _axdata_num(row.get("open")),
                "high": _axdata_num(row.get("high")),
                "low": _axdata_num(row.get("low")),
                "close": _axdata_num(row.get("close")),
                "volume": _axdata_num(row.get("volume")),
            }

    days = sorted(by_date)
    if trim_tail:
        n = int(count)
        if not 1 <= n <= 640:
            n = 320
        days = days[-n:]
    rows = [dict(by_date[d], code=symbol) for d in days]
    if not rows:
        return pl.DataFrame({k: [] for k in _KLINE_DTYPES}).cast(_KLINE_DTYPES)
    return pl.DataFrame(rows).cast(_KLINE_DTYPES)


def tencent_kline(code, period="day", adjust=None, start=None, end=None, count=320):
    """腾讯K线 — 日/周/月（默认前复权）。

    日线走 axdata 腾讯源（stock_zh_a_hist_tx），失败降级手抄件；
    周/月线 axdata 腾讯源无对应接口，直走手抄件（阶段二 2A 口径）。
    """
    if str(period).lower() == "day":
        try:
            return _axdata_tencent_kline(code, adjust=adjust, start=start,
                                         end=end, count=count)
        except Exception:  # noqa: BLE001 — axdata 包/网络失败一律降级手抄件
            pass
    return _tencent_kline_legacy(code, period=period, adjust=adjust,
                                 start=start, end=end, count=count)


# ============================================================
# 腾讯分时
# ============================================================

# Windows 环境 venv 未装 tzdata，ZoneInfo("Asia/Shanghai") 会抛
# ZoneInfoNotFoundError；北京时间 = UTC+8（1991 年后无夏令时），
# 用固定偏移与 hithink.py 的 _CST 同款写法。
_CST = timezone(timedelta(hours=8))


def _tencent_minute_call(symbol: str):
    """腾讯分时 API 调用（参数键是 code，轮换入口）"""
    last_err = None
    for host in _TENCENT_HOSTS:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{host}/appstock/app/minute/query",
                                  params={"code": symbol})
                resp.raise_for_status()
                data = resp.json().get("data", {})
                if data:
                    return data, host
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"腾讯分时所有入口均失败: {last_err}")


def tencent_intraday(code) -> dict:
    """腾讯分时 — 最近一个交易日的当日分钟线（盘中为今日实时，盘后/节假日回看上一交易日）。

    返回 `{"symbol","date","preclose","source","is_today","points"}`，
    points 元素 `[hhmm, price, volume, avg]`。解析口径（2026-10-13 真实响应实测）：
    - 腾讯原始行为 `"HHMM 价 累计量(手) 累计额(元)"` 共 4 段——第 4 段是
      **累计成交额**而非早期假设的均价，故 avg 由 累计额/(累计手×100) 换算
      （VWAP 均价线）；volume 由累计量差分为每分钟成交量（手）；
    - preclose 取 `node["qt"][symbol][4]`（与 tencent_quote 的 qt 索引惯例
      一致），缺失兜底 = 首点价；
    - 北交所不前置拒绝：统一尝试，失败抛 RuntimeError 由上层降级 UNAVAILABLE。
    """
    symbol = get_prefix(code) + norm_ticker(code)
    raw, _host = _tencent_minute_call(symbol)
    node = raw.get(symbol)
    if not isinstance(node, dict):
        raise RuntimeError(f"腾讯分时 {symbol} 响应缺个股节点，keys={list(raw.keys())}")
    inner = node.get("data")
    if not isinstance(inner, dict):
        raise RuntimeError(f"腾讯分时 {symbol} 响应缺 data，node keys={list(node.keys())}")
    rows = inner.get("data") or []
    if not rows:
        raise RuntimeError(f"腾讯分时 {symbol} 无分时行，inner keys={list(inner.keys())}")

    date_raw = str(inner.get("date") or "").strip()
    if not (len(date_raw) == 8 and date_raw.isdigit()):
        raise RuntimeError(f"腾讯分时 {symbol} date 缺失或非法: {date_raw!r}")
    day = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as e:
        raise RuntimeError(f"腾讯分时 {symbol} date 非法: {date_raw!r}") from e

    points = []
    prev_cum_vol = 0.0
    for line in rows:
        parts = str(line).split()
        if len(parts) < 3:
            continue
        try:
            price = float(parts[1])
            cum_vol = float(parts[2])
        except ValueError:
            continue
        if price <= 0:
            continue
        try:
            cum_amt = float(parts[3]) if len(parts) >= 4 else None
        except ValueError:
            cum_amt = None
        minute_vol = max(cum_vol - prev_cum_vol, 0.0)
        if cum_amt is not None and cum_vol > 0:
            avg = cum_amt / (cum_vol * 100.0)
            if avg <= 0:
                avg = price
        else:
            avg = price
        points.append([parts[0], price, minute_vol, avg])
        prev_cum_vol = cum_vol

    if not points:
        raise RuntimeError(f"腾讯分时 {symbol} 解析后 0 点（原始 {len(rows)} 行）")

    preclose = None
    qt = (node.get("qt") or {}).get(symbol)
    if isinstance(qt, (list, tuple)) and len(qt) > 4:
        preclose = _v39_num(qt[4])
    if not preclose or preclose <= 0:
        preclose = points[0][1]  # 缺失兜底 = 首点价

    today = datetime.now(_CST).date().isoformat()
    return {
        "symbol": symbol,
        "date": day,
        "preclose": preclose,
        "source": "tencent",
        "is_today": day == today,
        "points": points,
    }


# 开盘前（09:10 前）腾讯/axdata 的“当日”接口会回吐上一交易日数据；
# 且周末/节假日（含工作日休市）axdata today 接口同样回吐——其响应无任何
# 日期字段（2026-10-14 实测列：time_label/price/avg_price/volume 等，
# 无 trade_date/date 键），仅凭“非空 + 过 09:10”就把 date 标成今天会把
# 上一交易日误标 is_today=True（任务审查 Important 1）。故今日判定必须
# 过 _beijing_trading_day_today 交易日闸门，无法确定时保守走 history 回看。
_Intraday_SESSION_START = dt_time(9, 10)

# 交易日判定按日缓存（只存成功判定：真非交易日允许重取，首个交易时刻
# 能立刻翻真——缓存 False 会把整个交易日卡死在“非今日”）。
_TRADING_DAY_CACHE: dict = {"date": None, "ok": False}


def _beijing_trading_day_today(today: date | None = None) -> bool:
    """北京今日是否交易日：腾讯指数日K最后一根 == 北京今日。

    与 webapi 的 `_is_trading_day_today` 同判法（16:00 闸门 / 主线实时守卫
    的仓内惯例）。周末直接 False 不发请求；探测失败保守 False 且不落缓存
    （网络抖动不许毒化，失败不落缓存——同 webapi 教训注释）。
    """
    today = today or datetime.now(_CST).date()
    if today.weekday() >= 5:
        return False
    if _TRADING_DAY_CACHE["date"] == today.isoformat() and _TRADING_DAY_CACHE["ok"]:
        return True
    try:
        data, _host = _tencent_kline_call(
            "/appstock/app/fqkline/get", "sh000001,day,,,5,qfq")
        node = data.get("sh000001") or {}
        rows = node.get("qfqday") or node.get("day") or []
        ok = bool(rows) and str(rows[-1][0])[:10] == today.isoformat()
    except Exception:
        return False   # 失败不落缓存：一次网络抖动不许毒化全天判定
    if ok:
        _TRADING_DAY_CACHE["date"] = today.isoformat()
        _TRADING_DAY_CACHE["ok"] = True
    return ok


def _axdata_client():
    """axdata 客户端工厂（惰性 import：无 axdata 的环境只在真正取数时报错）。"""
    from axdata import AxDataClient
    return AxDataClient()


def axdata_intraday(code, now: datetime | None = None) -> dict:
    """axdata 通达信分时 — 最近交易日的分钟线（与 tencent_intraday 同 shape）。

    返回 `{"symbol","date","preclose","source","is_today","points"}`，
    points 元素 `[hhmm, price, volume, avg]`。口径（2026-10-13 真实响应实测）：
    - `stock_intraday_today_tdx` 返回**每分钟** bar（time_label "09:31" /
      price / avg_price / volume），volume 单位为**手**（首根含集合竞价量），
      与腾讯源差分后的每分钟手数同口径；
    - `stock_intraday_history_tdx`（按 trade_date 查历史交易日）有
      trade_time/price/volume/prev_close 但无 avg_price，均价用
      Σ(分价×分量)/Σ分量 近似 VWAP；
    - 开盘前（<09:10）与非交易日（周末/节假日）today 接口会回吐上一交易
      日且响应不带日期 → 走 history 回看（≤7 个自然日）定位真实交易日；
      “今日”标签须过 `_beijing_trading_day_today` 交易日闸门，无法确定时
      保守判非今日；北交所不前置拒绝，失败如实降级。
    """
    symbol = get_prefix(code) + norm_ticker(code)
    client = _axdata_client()
    now = now or datetime.now(_CST)

    # ① 当日时段（≥09:10）且交易日闸门确认北京今日有 session：today 接口 =
    #    今日实时/当日全量（含 avg_price）。闸门不过（周末/节假日回吐、
    #    判定失败）一律落入②，宁可 is_today=False 不冒充实时。
    today_pdf = None
    try:
        today_pdf = client.call("stock_intraday_today_tdx", code=symbol)
    except Exception:
        today_pdf = None
    if (today_pdf is not None and len(today_pdf)
            and now.time() >= _Intraday_SESSION_START
            and _beijing_trading_day_today(now.date())):
        points = []
        for r in today_pdf.to_dict("records"):
            label = str(r.get("time_label") or "").replace(":", "")
            price = _v39_num(r.get("price"))
            if not label or price is None or price <= 0:
                continue
            points.append([label, price, float(r.get("volume") or 0),
                           _v39_num(r.get("avg_price")) or price])
        if not points:
            raise RuntimeError(f"axdata 分时 {symbol} 解析后 0 点")
        preclose = None
        try:  # 昨收：实时快照 pre_close（盘中 history 可能未落档）
            snap = client.call("stock_realtime_snapshot_tdx", code=symbol)
            if snap is not None and len(snap):
                preclose = _v39_num(snap.to_dict("records")[0].get("pre_close"))
        except Exception:
            pass
        if not preclose or preclose <= 0:
            preclose = points[0][1]
        return {"symbol": symbol, "date": now.date().isoformat(),
                "preclose": preclose, "source": "axdata",
                "is_today": True, "points": points}

    # ② 开盘前 / 交易日闸门未过 / today 空档：回看 history 定位最近有
    #    分时的交易日（≤7 天）；history 按 trade_date 查，天然带真实日期
    for i in range(7):
        d = now.date() - timedelta(days=i)
        ymd = d.strftime("%Y%m%d")
        try:
            pdf = client.call("stock_intraday_history_tdx",
                              code=symbol, trade_date=ymd)
        except Exception:
            pdf = None
        if pdf is None or not len(pdf):
            continue
        rows = pdf.to_dict("records")
        points = []
        cum_pv = cum_v = 0.0
        for r in rows:
            price = _v39_num(r.get("price"))
            if price is None or price <= 0:
                continue
            vol = float(r.get("volume") or 0)
            cum_pv += price * vol
            cum_v += vol
            avg = cum_pv / cum_v if cum_v > 0 else price
            t = str(r.get("trade_time") or "")  # "2026-10-13T09:31:00+08:00"
            hhmm = (t[11:13] + t[14:16]) if len(t) >= 16 else ""
            if not hhmm:
                continue
            points.append([hhmm, price, vol, avg])
        if not points:
            raise RuntimeError(f"axdata 分时 {symbol} {ymd} 解析后 0 点")
        preclose = _v39_num(rows[0].get("prev_close"))
        if not preclose or preclose <= 0:
            preclose = points[0][1]
        return {"symbol": symbol, "date": d.isoformat(),
                "preclose": preclose, "source": "axdata",
                "is_today": i == 0, "points": points}

    raise RuntimeError(f"axdata 分时 {symbol} 无数据（近 7 天无分时档）")


def _hist_bars(code, day: str):
    """取指定交易日分钟档 → (status, bars, preclose)。
    bars=[(hhmm, price, volume)] 逐根（hhmm="09:47"，触发备注用）；
    status ∈ "ok" / "no_data"（无档/请求失败/缺昨收——买卖条件扫描共用，
    失败语义交由调用方定）。"""
    symbol = get_prefix(code) + norm_ticker(code)
    ymd = str(day).replace("-", "")
    try:
        pdf = _axdata_client().call("stock_intraday_history_tdx",
                                    code=symbol, trade_date=ymd)
    except Exception:
        return "no_data", [], None
    if pdf is None or len(pdf) == 0:
        return "no_data", [], None
    bars: list[tuple[str, float, float]] = []
    preclose = None
    for r in pdf.to_dict("records"):
        price = _v39_num(r.get("price"))
        if price is None or price <= 0:
            continue
        vol = _v39_num(r.get("volume")) or 0.0
        if preclose is None:
            preclose = _v39_num(r.get("prev_close"))
        t = str(r.get("trade_time") or "")
        hhmm = t[11:16] if len(t) >= 16 else ""  # "09:36"（触发备注展示）
        bars.append((hhmm, float(price), float(vol)))
    if not bars or not preclose or preclose <= 0:
        return "no_data", [], None
    return "ok", bars, float(preclose)


def _scan_trigger(bars, preclose, *, rising: bool):
    """买/卖条件共用扫描：从第 6 根（09:36）起逐分钟找首个满足
    「价格 vs 昨收」与「当分钟量 vs 前 5 分钟均量」同向不等式的一根。
    rising=True：价格>昨收 且 量>均量（买·放量上攻）；
    rising=False：价格<昨收 且 量<均量（卖·跌缩量走弱）。均严格不等。
    命中返回触发备注 dict（成交时记进台账"满足当时的条件"）：
    {at, price, prev_close, vol, avg5}；未命中 None。"""
    for i in range(5, len(bars)):  # 前 5 根齐才判
        at, price, vol = bars[i]
        avg5 = sum(v for _, _, v in bars[i - 5:i]) / 5.0
        if rising and price > preclose and vol > avg5:
            return {"at": at, "price": price, "prev_close": preclose,
                    "vol": vol, "avg5": round(avg5, 2)}
        if not rising and price < preclose and vol < avg5:
            return {"at": at, "price": price, "prev_close": preclose,
                    "vol": vol, "avg5": round(avg5, 2)}
    return None


def intraday_entry_trigger(code, day: str) -> tuple[str, dict | None]:
    """开盘后买入条件扫描（2026-11-04 拍板，替代 10:30 定点取价）。

    axdata 历史分时从 09:31 起逐分钟找**首个**同时满足：
    - 上涨：该分钟价 > 昨收（prev_close，raw 口径）
    - 放量：该分钟成交量 > 前 5 分钟平均每分钟成交量（均严格大于，
      最早第 6 根即 09:36 起判）
    返回 (status, result)：
    - ("ok", trigger)  触发备注 dict {at, price, prev_close, vol, avg5}
      ——price 为成交价来源，其余字段随成交记台账（满足当时的条件）
    - ("no_trigger", None) 有分时但全天未满足
    - ("no_data", None)    无分时档/请求失败/缺昨收（调用方备注原因）
    """
    status, bars, preclose = _hist_bars(code, day)
    if status != "ok":
        return "no_data", None
    hit = _scan_trigger(bars, preclose, rising=True)
    return ("ok", hit) if hit else ("no_trigger", None)


def intraday_exit_trigger(code, day: str) -> tuple[str, dict | None]:
    """卖出条件扫描（2026-11-04 拍板，与买入镜像——冲高回落/非主线类
    持仓次日开盘后执行）。

    从 09:31 起逐分钟找**首个**同时满足：
    - 下跌：该分钟价 < 昨收
    - 缩量：该分钟成交量 < 前 5 分钟平均每分钟成交量（均严格小于，
      最早第 6 根即 09:36 起判）
    返回语义同 intraday_entry_trigger——("ok", trigger) 的 trigger.price
    为成交价，{at, prev_close, vol, avg5} 随成交记台账；("no_trigger",
    None) 全天未满足（调用方留仓次日再扫，11-04 拍板"一直等"）；
    ("no_data", None) 缺数据。
    """
    status, bars, preclose = _hist_bars(code, day)
    if status != "ok":
        return "no_data", None
    hit = _scan_trigger(bars, preclose, rising=False)
    return ("ok", hit) if hit else ("no_trigger", None)


# ============================================================
# 通达信盘后包
# ============================================================

TDX_PACKAGE_URL = "https://www.tdx.com.cn/products/data/data/g4day/{ymd}.zip"
TDX_BJ_FIRST_DAY = "20220506"
TDX_MIN_PRICED = {"sh": 10000, "sz": 3000, "bj": 50}


def _tdx_parse_package(content, ymd):
    """解析通达信每日增量包"""
    import math
    import re
    import struct
    import zipfile

    archive = zipfile.ZipFile(io.BytesIO(content))
    names = set(archive.namelist())
    rows = []

    for market in ("sh", "sz", "bj"):
        cod_name, md1_name = f"{market}{ymd[2:]}.cod", f"{market}{ymd[2:]}.md1"
        if market == "bj" and ymd < TDX_BJ_FIRST_DAY and cod_name not in names and md1_name not in names:
            continue
        if cod_name not in names or md1_name not in names:
            raise RuntimeError(f"通达信盘后包缺少 {cod_name}/{md1_name}")
        cod, md1 = archive.read(cod_name), archive.read(md1_name)
        if len(cod) % 150 or len(md1) % 512:
            raise RuntimeError(f"{market} 文件长度异常")
        if len(cod) // 150 != len(md1) // 512:
            raise RuntimeError(f"{market} 代码表/行情块数对不上")

        before = len(rows)
        codes, seqs = set(), set()
        for offset in range(0, len(cod), 150):
            record = cod[offset:offset + 150]
            code = record[0:6].rstrip(b"\x00 ").decode("ascii", "replace")
            seq = struct.unpack("<H", record[32:34])[0]
            if not re.fullmatch(r"[0-9]{6}", code):
                continue
            if code in codes or seq in seqs:
                continue
            codes.add(code)
            seqs.add(seq)
            block = md1[seq * 512:(seq + 1) * 512]
            if len(block) != 512:
                continue
            prev_close = struct.unpack("<d", block[4:12])[0]
            open_, high, low, close = struct.unpack("<4d", block[12:44])
            amount = struct.unpack("<d", block[72:80])[0]
            if not all(math.isfinite(v) for v in (prev_close, open_, high, low, close, amount)):
                continue
            if close <= 0:
                continue
            volume = struct.unpack("<Q", block[56:64])[0]
            raw_name = record[40:72].split(b"\x00")[0]
            try:
                name = raw_name.decode("gbk").strip()
            except UnicodeDecodeError:
                name = f"未知{code}"
            if not name:
                continue

            # 转换为 thscode 格式
            if market == "bj" or code.startswith("920"):
                thscode = f"{code}.BJ"
            elif code.startswith(("6", "9")):
                thscode = f"{code}.SH"
            else:
                thscode = f"{code}.SZ"

            rows.append({
                "thscode": thscode,
                "date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}",
                "open": round(open_, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": volume,
                "amount": round(amount, 2),
            })

        if len(rows) - before < TDX_MIN_PRICED[market]:
            # 北交所可能数据较少，不强制报错
            if market != "bj":
                raise RuntimeError(f"通达信盘后包 {market} 数据过少")

    return rows


def tdx_daily_package(date):
    """通达信官网每日盘后包 — 某一交易日沪深北全部证券的日线。"""
    import polars as pl

    ymd = _v39_date(date).replace("-", "")
    url = TDX_PACKAGE_URL.format(ymd=ymd)

    with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        response = client.get(url)

    if response.status_code == 404:
        raise ValueError(f"{date} 没有通达信盘后包（非交易日或包未发布）")

    if not response.content.startswith(b"PK"):
        raise RuntimeError("通达信盘后包不是 zip 文件")

    rows = _tdx_parse_package(response.content, ymd)
    return pl.DataFrame(rows)


# ============================================================
# a-stock-data 适配器
# ============================================================

# ============================================================
# 腾讯快照（阶段二 2A：axdata 腾讯源优先 + 手抄件降级）
# ============================================================

def _tencent_quote_axdata(codes: list[str]) -> list[dict]:
    """axdata 腾讯快照（tencent_realtime_snapshot）→ 手抄件同构 item 列表。

    口径对齐（2026-11-01 灰度实测）：volume 手、amount 元（手抄件 ×1e4 同值）、
    time=YYYYMMDDHHMMSS、change 用与手抄件相同的 round(price−prev,4) 算式
    （不用 axdata 的 change 字段，避免源端四舍五入差异）、市值 axdata 是**亿元**
    → ×1e8 转元；NaN/inf → None（与 _v39_num 缺失即 None 同语义）。
    """
    instruments = [_axdata_code(c) for c in codes]
    client = _axdata_client()
    pdf = client.call("tencent_realtime_snapshot", code=instruments)
    items = []
    for row in (pdf.to_dict("records") if pdf is not None and len(pdf) else []):
        price = _axdata_num(row.get("last_price"))
        prev = _axdata_num(row.get("pre_close"))
        mcap_yi = _axdata_num(row.get("total_market_value"))
        items.append({
            "symbol": str(row.get("symbol") or ""),
            "name": row.get("name"),
            "price": price,
            "open": _axdata_num(row.get("open")),
            "high": _axdata_num(row.get("high")),
            "low": _axdata_num(row.get("low")),
            "prev_close": prev,
            "change": (round(price - prev, 4)
                       if price is not None and prev is not None else None),
            "change_pct": _axdata_num(row.get("change_pct")),
            "volume": _axdata_num(row.get("volume")),
            "amount": _axdata_num(row.get("amount")),
            "turnover_ratio": _axdata_num(row.get("turnover_rate")),
            "pe_ttm": _axdata_num(row.get("pe_dynamic")),
            "pb": _axdata_num(row.get("pb")),
            "market_cap": (mcap_yi * 1e8 if mcap_yi is not None else None),
            "time": row.get("quote_time"),
        })
    return items


def _tencent_quote_legacy(codes: list[str]) -> list[dict]:
    """腾讯快照手抄件（qt.gtimg.cn GBK 直连）——阶段二 2A 起为降级腿。"""
    # 前缀路由
    prefixed = []
    for c in codes:
        low = c.lower()
        if low.startswith(("sh", "sz", "bj")):
            p = low
        elif c.startswith("92"):
            p = f"bj{c}"
        elif c in _SH_INDEX or c.startswith(("5", "6", "9")):
            p = f"sh{c}"
        elif c.startswith(("4", "8")):
            p = f"bj{c}"
        else:
            p = f"sz{c}"
        prefixed.append(p)

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    import urllib.request
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk")

    items = []
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 48:
            continue
        code = key[2:] if key.startswith(("sh", "sz", "bj")) else key
        price = _v39_num(vals[3])
        prev_close = _v39_num(vals[4])
        items.append({
            "symbol": code,
            "name": vals[1],
            "price": price,
            "open": _v39_num(vals[5]),
            "high": _v39_num(vals[33]),
            "low": _v39_num(vals[34]),
            "prev_close": prev_close,
            "change": (round(price - prev_close, 4)
                       if price is not None and prev_close is not None else None),
            "change_pct": _v39_num(vals[32]),
            "volume": _v39_num(vals[6]),
            "amount": _v39_num(vals[37]) * 1e4 if vals[37] else None,
            "turnover_ratio": _v39_num(vals[38]),
            "pe_ttm": _v39_num(vals[39]),
            "pb": _v39_num(vals[46]),
            "market_cap": _v39_num(vals[45]) * 1e8 if vals[45] else None,
            "time": vals[30] if len(vals) > 30 else None,
        })
    return items


# ============================================================
# 腾讯逐笔（阶段二 2A：axdata 腾讯源；手抄件解析分隔符写错恒 0 条，已弃）
# ============================================================

def _tencent_ticks_axdata(code: str) -> list[dict]:
    """axdata 腾讯逐笔（stock_zh_a_tick_tx_js）→ {time,price,volume,amount,side}。

    口径（2026-11-01 实测）：volume 手、amount 元；side 为 buy/sell/neutral
    （手抄件原设计的 B/S 从未真正输出过——旧解析按 `~` 分割而源格式是
    `/`+`|` 分隔，恒 0 条，本腿为纯修复）；北交所腾讯源无数据，进本函数前
    由调用方拒绝。空返回（代码无效/无成交）抛 ValueError 如实上报。
    """
    prefix = get_prefix(code)
    if prefix == "bj":
        raise ValueError("腾讯逐笔不支持北交所")
    instrument = _axdata_code(code)
    client = _axdata_client()
    pdf = client.call("stock_zh_a_tick_tx_js", code=instrument, limit=500)
    items = []
    for row in (pdf.to_dict("records") if pdf is not None and len(pdf) else []):
        items.append({
            "time": row.get("trade_time"),
            "price": _axdata_num(row.get("price")),
            "volume": _axdata_num(row.get("volume")),
            "amount": _axdata_num(row.get("amount")),
            "side": str(row.get("trade_side") or ""),
        })
    if not items:
        raise ValueError(f"腾讯逐笔 {instrument} 无数据（代码无效或无成交）")
    return items


# ============================================================
# 东财三端点（阶段二 2B：axdata 东财源优先 + 手抄件降级）
# ============================================================

def _ymd8_to_dash(s) -> str:
    """紧凑 YYYYMMDD → YYYY-MM-DD；已是短横格式则截前 10 位。"""
    d = str(s or "")
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d[:10]


def _dragon_tiger_axdata(code: str, trade_date: str) -> list[dict]:
    """axdata 东财龙虎榜（eastmoney_dragon_tiger_daily）→ 手抄件同构 item。

    语义（2026-11-01 真机对照逐字段一致）：axdata 为按日全市场榜单
    （精确单日），客户端按 code 过滤 + 截 10 条（旧件 pageSize=10 同上限）。
    net_buy：原始列 BILLBOARD_NET_AMT 单位**元**（2026-11-02 四重验证：
    东财官网 JS zoom:-4 除 1 万显示万、全市场当日净额 26 亿量级、买卖
    自洽），→ 亿元 round 2 位（用户拍板方案 B，前端"亿"标签）；旧件
    /10000 出的是万元被前端标成亿、放大 10⁴，已修。
    旧件显式日期为 since 语义（TRADE_DATE>=），axdata 为
    精确单日——前端只走无日期路径（今日榜单），仅 /dragon-tiger/detail
    手动传日期时口径收窄。
    """
    bare = norm_ticker(code)
    client = _axdata_client()
    pdf = client.call("eastmoney_dragon_tiger_daily",
                      trade_date=_v39_date(trade_date), limit=200)
    items = []
    for row in (pdf.to_dict("records") if pdf is not None and len(pdf) else []):
        if str(row.get("symbol") or "") != bare:
            continue
        items.append({
            "date": _ymd8_to_dash(row.get("trade_date")),
            "reason": str(row.get("reason") or ""),
            "net_buy": round((_axdata_num(row.get("net_buy_amount")) or 0.0)
                             / 1e8, 2),
            "turnover": round(_axdata_num(row.get("turnover_rate")) or 0.0, 2),
        })
    return items[:10]


# 龙虎榜"最近上榜记录"窗口的空结果负缓存：code → 过期时间戳。
# 没有它，"从未上榜"的股票每次打开个股页都要付满 10 个交易日 × ~1.5s
# 的逐日查询（2026-11-02 实测窗口全空 ~15s）。10 分钟内同一 code 直接
# 返回空；窗口随交易日推进，TTL 远短于一天。
_DT_NEG_CACHE: dict[str, float] = {}
_DT_NEG_TTL = 600.0


def _recent_trading_days(limit: int = 10) -> list[str]:
    """≤ 今天的最近交易日（新→旧）。本地日历不可用时退回工作日近似。"""
    today = date.today()
    try:
        from ..config import load_config
        from ..store import market_data as md
        cal = [str(d)[:10] for d in md.calendar_days(load_config())]
        out = [d for d in cal if d <= today.isoformat()]
        return out[-limit:][::-1]
    except Exception:  # noqa: BLE001 — 日历不可用用工作日近似（节假日多查一天无害）
        out: list[str] = []
        d = today
        while len(out) < limit and (today - d).days <= 21:
            if d.weekday() < 5:
                out.append(d.isoformat())
            d -= timedelta(days=1)
        return out


def _dragon_tiger_legacy(code: str, trade_date: str) -> list[dict]:
    """龙虎榜手抄件（东财 datacenter 直连，since 语义）——2B 降级腿。

    trade_date 起点语义（TRADE_DATE>=）：区间窗口一把抓，正是"最近上榜
    记录"路径降级时的正确用法。
    """
    code = norm_ticker(code)
    REPORT_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")(TRADE_DATE>=\'{trade_date}\')',
        "pageSize": "10",
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(REPORT_API, params=params,
                         headers={"Referer": "https://data.eastmoney.com/"})
        d = resp.json()
        data = (d.get("result") or {}).get("data") or []

    items = []
    for row in data[:10]:
        items.append({
            "date": str(row.get("TRADE_DATE", ""))[:10],
            "reason": row.get("EXPLANATION", ""),
            # BILLBOARD_NET_AMT 单位元 → 亿元（2026-11-02 与 axdata 腿
            # 同步修复量纲：旧 /10000 出万元被前端标成亿，放大 10⁴）
            "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 1e8, 2),
            "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return items


def _margin_trading_axdata(code: str, limit: int = 30) -> list[dict]:
    """axdata 东财融资融券（eastmoney_margin_trading）→ 手抄件同构 item。

    同上游 RPTA_WEB_RZRQ_GGMX，2026-11-01 真机对照字段值零差异，
    仅日期紧凑→短横；axdata 单页上限 200（webapi 允许 limit≤500），
    分页取满，短页/5 页封顶防 runaway。
    """
    instrument = _axdata_code(code)
    client = _axdata_client()
    limit = max(int(limit), 1)
    items: list[dict] = []
    page = 1
    while len(items) < limit and page <= 5:
        want = min(200, limit - len(items))
        pdf = client.call("eastmoney_margin_trading", code=instrument,
                          page=page, limit=want)
        rows = pdf.to_dict("records") if pdf is not None and len(pdf) else []
        for row in rows:
            items.append({
                "date": _ymd8_to_dash(row.get("trade_date")),
                "rzye": _axdata_num(row.get("margin_balance")),
                "rzmre": _axdata_num(row.get("margin_buy_amount")),
                "rzche": _axdata_num(row.get("margin_repay_amount")),
                "rqye": _axdata_num(row.get("short_balance")),
                "rqmcl": _axdata_num(row.get("short_sell_volume")),
                "rqchl": _axdata_num(row.get("short_repay_volume")),
                "rzrqye": _axdata_num(row.get("total_balance")),
            })
        if len(rows) < want:
            break
        page += 1
    return items[:limit]


def _margin_trading_legacy(code: str, limit: int = 30) -> list[dict]:
    """融资融券手抄件（东财 datacenter 直连）——2B 降级腿。"""
    code = norm_ticker(code)
    DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPTA_WEB_RZRQ_GGMX",
        "columns": "ALL",
        "filter": f'(SCODE="{code}")',
        "pageSize": str(limit),
        "sortColumns": "DATE",
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.get(DATACENTER_URL, params=params,
                         headers={"Referer": "https://data.eastmoney.com/"})
        d = resp.json()
        data = (d.get("result") or {}).get("data") or []

    items = []
    for row in data[:limit]:
        items.append({
            "date": str(row.get("DATE", ""))[:10],
            "rzye": _v39_num(row.get("RZYE")),
            "rzmre": _v39_num(row.get("RZMRE")),
            "rzche": _v39_num(row.get("RZCHE")),
            "rqye": _v39_num(row.get("RQYE")),
            "rqmcl": _v39_num(row.get("RQMCL")),
            "rqchl": _v39_num(row.get("RQCHL")),
            "rzrqye": _v39_num(row.get("RZRQYE")),
        })
    return items


def _eastmoney_reports_axdata(code: str, max_pages: int = 3) -> list[dict]:
    """axdata 东财研报（eastmoney_research_reports）→ 手抄件同构 item。

    同上游 reportapi（2026-11-01 真机对照：标题/日期/评级/今年EPS 一致）；
    两处语义差：orgSName 由简称变全称（axdata 只暴露 orgName 全称）、
    predictNextYearEps 无对应字段 → None（前端未消费该字段）；
    predictThisYearEps 由字符串变 float。单页 100 条，满 20 即停
    （旧件整页累积后截 20，输出等价）。
    """
    instrument = _axdata_code(code)
    client = _axdata_client()
    items: list[dict] = []
    page = 1
    while page <= max(int(max_pages), 1) and len(items) < 20:
        pdf = client.call("eastmoney_research_reports", code=instrument,
                          page=page, limit=100)
        rows = pdf.to_dict("records") if pdf is not None and len(pdf) else []
        for row in rows:
            if len(items) >= 20:
                break
            items.append({
                "title": str(row.get("title") or ""),
                "publishDate": _ymd8_to_dash(row.get("publish_date")),
                "orgSName": str(row.get("org_name") or ""),
                "emRatingName": str(row.get("rating") or ""),
                "predictNextYearEps": None,
                "predictThisYearEps": _axdata_num(
                    row.get("eps_forecast_this_year")),
            })
        if len(rows) < 100:
            break
        page += 1
    return items


def _eastmoney_reports_legacy(code: str, max_pages: int = 3) -> list[dict]:
    """东财研报手抄件（reportapi 直连，分页累积后截 20）——2B 降级腿。"""
    code = norm_ticker(code)
    REPORT_API = "https://reportapi.eastmoney.com/report/list"
    all_records = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2024-01-01", "endTime": "2030-01-01",
            "pageNo": str(page), "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": str(page), "pageNum": str(page), "pageNumber": str(page),
        }
        with httpx.Client(timeout=30) as client:
            resp = client.get(REPORT_API, params=params,
                              headers={"Referer": "https://data.eastmoney.com/"})
            d = resp.json()
            rows = d.get("data") or []
            if not rows:
                break
            all_records.extend(rows)
            if page >= (d.get("TotalPage", 1) or 1):
                break

    items = []
    for r in all_records[:20]:  # 限制返回数量
        items.append({
            "title": r.get("title", ""),
            "publishDate": r.get("publishDate", "")[:10],
            "orgSName": r.get("orgSName", ""),
            "emRatingName": r.get("emRatingName", ""),
            "predictNextYearEps": r.get("predictNextYearEps"),
            "predictThisYearEps": r.get("predictThisYearEps"),
        })
    return items


def _eps_forecast_axdata(code: str) -> list[dict]:
    """axdata 通达信盈利预测一致预期（stock_forecast_consensus_tdx）→ {year, eps}。

    2C 字段比对（2026-11-01）：旧件正则爬同花顺 worth.html 三只实测股
    **恒空**（页面结构早已变化，死端点），axdata 给 forecast_start_year +
    三年 eps_year1/2/3 干净数据；按 year=基准年+0/1/2 展开，None/NaN
    过滤，保留旧件最多 5 条的上限。
    """
    instrument = _axdata_code(code)
    client = _axdata_client()
    pdf = client.call("stock_forecast_consensus_tdx", code=instrument)
    items: list[dict] = []
    for row in (pdf.to_dict("records") if pdf is not None and len(pdf) else []):
        base = row.get("forecast_start_year")
        if base is None:
            continue
        try:
            base = int(base)
        except (TypeError, ValueError):
            continue
        for i, key in enumerate(("eps_year1", "eps_year2", "eps_year3")):
            eps = _axdata_num(row.get(key))
            if eps is not None:
                items.append({"year": base + i, "eps": eps})
    return items[:5]


class AStockDataAdapter(Adapter):
    """a-stock-data 适配器：封装腾讯K线和通达信盘后包"""

    name = "a_stock_data"
    source = "a-stock-data"
    freshness_kind = "daily"

    def __init__(self, cfg: dict):
        super().__init__(cfg)

    def tencent_kline(self, code: str, start: str = None, end: str = None,
                      period: str = "day", adjust: str = None, count: int = 320
                      ) -> models.DimensionResult:
        """腾讯K线数据"""
        try:
            with timed() as t:
                df = tencent_kline(code, period=period, adjust=adjust,
                                   start=start, end=end, count=count)
            items = []
            for row in df.iter_rows(named=True):
                items.append({
                    "symbol": row.get("code"),
                    "date": row.get("date"),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                })
            return self.ok({"item": items, "total": len(items)},
                           as_of=items[-1]["date"] if items else None,
                           endpoint="a-stock-data/tencent_kline",
                           latency_ms=t["ms"],
                           note=f"腾讯K线 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"腾讯K线失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def tencent_intraday(self, code: str) -> models.DimensionResult:
        """腾讯分时（最近交易日分钟线，盘中为今日实时）"""
        try:
            with timed() as t:
                payload = tencent_intraday(code)
            return self.ok(payload,
                           as_of=payload["date"],
                           endpoint="a-stock-data/tencent_intraday",
                           latency_ms=t["ms"],
                           kind="realtime",
                           note=f"腾讯分时 {len(payload['points'])} 点")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"腾讯分时获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def axdata_intraday(self, code: str) -> models.DimensionResult:
        """axdata 通达信分时（最近交易日分钟线，盘中为今日实时）"""
        try:
            with timed() as t:
                payload = axdata_intraday(code)
            return self.ok(payload,
                           as_of=payload["date"],
                           endpoint="a-stock-data/axdata_intraday",
                           latency_ms=t["ms"],
                           kind="realtime",
                           note=f"axdata分时 {len(payload['points'])} 点")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"axdata分时获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def tdx_daily(self, date_str: str) -> models.DimensionResult:
        """通达信盘后包全市场日线"""
        try:
            with timed() as t:
                df = tdx_daily_package(date_str)
            items = df.to_dicts()
            return self.ok({"item": items, "total": len(items)},
                           as_of=date_str,
                           endpoint="a-stock-data/tdx_daily",
                           latency_ms=t["ms"],
                           note=f"通达信盘后包 {len(items)} 条")
        except ValueError as e:
            return self.fail(models.NOT_FOUND, str(e),
                             endpoint="a-stock-data/tdx_daily")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"通达信盘后包失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def health(self) -> models.DimensionResult:
        """连通性检查"""
        try:
            with timed() as t:
                _tencent_kline_call("/appstock/app/fqkline/get",
                                    "sh000001,day,,,3,")
            return self.ok({"status": "ok"}, endpoint="a-stock-data", latency_ms=t["ms"])
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"a-stock-data 不可用：{e}",
                             retryable=True, endpoint="a-stock-data")

    def tencent_quote(self, codes: list[str]) -> models.DimensionResult:
        """腾讯实时行情（批量）—— axdata 腾讯源优先，失败降级手抄件。"""
        try:
            with timed() as t:
                try:
                    items = _tencent_quote_axdata(codes)
                    leg = "axdata"
                except Exception:  # noqa: BLE001 — axdata 包/网络失败降级手抄件
                    items = _tencent_quote_legacy(codes)
                    leg = "legacy"
            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/tencent_quote",
                           latency_ms=t["ms"],
                           note=f"腾讯行情 {len(items)} 条（{leg}）")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"腾讯行情失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def eastmoney_reports(self, code: str, max_pages: int = 3) -> models.DimensionResult:
        """东财研报列表 —— axdata 东财源优先，失败降级手抄件。"""
        try:
            with timed() as t:
                try:
                    items = _eastmoney_reports_axdata(code, max_pages=max_pages)
                    leg = "axdata"
                except Exception:  # noqa: BLE001 — axdata 包/网络失败降级手抄件
                    items = _eastmoney_reports_legacy(code, max_pages=max_pages)
                    leg = "legacy"
            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/eastmoney_reports",
                           latency_ms=t["ms"],
                           note=f"东财研报 {len(items)} 条（{leg}）")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"东财研报失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def ths_eps_forecast(self, code: str) -> models.DimensionResult:
        """机构一致预期EPS —— axdata 通达信一致预期（无降级腿）。

        旧件（正则爬同花顺 worth.html）2026-11-01 实测三只股恒空，为死端点，
        不做降级；axdata 失败如实 UNAVAILABLE。item shape 保持 {year, eps}。
        """
        try:
            with timed() as t:
                items = _eps_forecast_axdata(code)
            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/ths_eps_forecast",
                           latency_ms=t["ms"],
                           note=f"一致预期 {len(items)} 条（axdata）")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"一致预期获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def dragon_tiger(self, code: str, trade_date: str = None) -> models.DimensionResult:
        """龙虎榜数据 —— axdata 东财源优先，失败降级手抄件。

        - 显式 trade_date：精确单日（/dragon-tiger/detail 手动传日期用）。
        - 无日期（前端默认路径）：**最近上榜记录**——自最近交易日起逐日
          回溯至多 10 个交易日，凑满 10 条或窗口用尽即停；空结果按 code
          负缓存 10 分钟（防"从未上榜"股每次页面加载付满窗口 ~15s）。
          背景（2026-11-02 用户报"个股分析龙虎榜不显示"）：此前无日期
          = 只查"今日"，非盘后时段（凌晨/周末/盘前）今日榜单未发布必然
          为空；而组件本就按"龙虎榜记录（近N次）"渲染——语义对齐组件。
        """
        bare = norm_ticker(code)
        try:
            with timed() as t:
                if trade_date:
                    window = [trade_date]
                    as_of = trade_date
                else:
                    cached = _DT_NEG_CACHE.get(bare)
                    if cached and time.time() < cached:
                        return self.ok(
                            {"item": [], "total": 0},
                            as_of=datetime.now().strftime("%Y-%m-%d"),
                            endpoint="a-stock-data/dragon_tiger",
                            latency_ms=0,
                            note="龙虎榜 0 条（近期空负缓存）")
                    window = _recent_trading_days(10)  # 新→旧
                    as_of = window[0] if window else datetime.now().strftime("%Y-%m-%d")
                try:
                    items: list[dict] = []
                    for d in window:
                        items.extend(_dragon_tiger_axdata(code, d))
                        if len(items) >= 10:
                            break
                    items = items[:10]
                    leg = "axdata"
                except Exception:  # noqa: BLE001 — axdata 包/网络失败降级手抄件
                    # since 语义一把抓整个窗口（显式日期路径窗口=单日，等价精确）
                    items = _dragon_tiger_legacy(code, window[-1])[:10]
                    leg = "legacy"
                if items:
                    as_of = items[0]["date"]
                elif not trade_date:
                    _DT_NEG_CACHE[bare] = time.time() + _DT_NEG_TTL
                    if len(_DT_NEG_CACHE) > 500:  # 防无界增长：顺手清过期项
                        now = time.time()
                        for k in [k for k, exp in _DT_NEG_CACHE.items()
                                  if exp < now]:
                            _DT_NEG_CACHE.pop(k, None)
            return self.ok({"item": items, "total": len(items)},
                           as_of=as_of,
                           endpoint="a-stock-data/dragon_tiger",
                           latency_ms=t["ms"],
                           note=f"龙虎榜 {len(items)} 条（{leg}）",
                           # 记录型数据：as_of 如实标注实际上榜日即可，
                           # 不让新鲜度把"8 天前上榜"翻成 stale 拦掉前端
                           status=models.OK)
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"龙虎榜获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def fund_flow(self, code: str) -> models.DimensionResult:
        """个股资金流向（当日分钟级）"""
        return self._fflow_kline(code, klt=1, tag="fund_flow")

    def fund_flow_daily(self, code: str, days: int = 120) -> models.DimensionResult:
        """个股资金流向（日级历史）：主力/超大单/大单/中单/小单净流入。

        东财 push2his 同一端点、klt=101 即日频，默认返回最近约 120 个交易日，
        `days` 通过 lmt 参数控制（0=全部）。
        """
        return self._fflow_kline(code, klt=101, tag="fund_flow_daily",
                                 lmt=max(0, int(days)))

    def _fflow_kline(self, code: str, *, klt: int, tag: str,
                     lmt: int | None = None) -> models.DimensionResult:
        """东财 fflow/kline 统一取数：klt=1 分钟级（push2）、klt=101 日级（push2his）。"""
        try:
            with timed() as t:
                code = norm_ticker(code)
                # em_secid: 1=沪市, 0=深市
                if code.startswith(("6", "9")) or code.startswith("5"):
                    secid = f"1.{code}"
                else:
                    secid = f"0.{code}"

                host = ("https://push2his.eastmoney.com" if klt == 101
                        else "https://push2.eastmoney.com")
                url = f"{host}/api/qt/stock/fflow/kline/get"
                params = {
                    "secid": secid, "klt": klt,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                }
                if lmt:
                    params["lmt"] = str(lmt)
                headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                }
                d = None
                last_exc = None
                for attempt in range(3):  # 东财偶发断连/风控，重试 3 次（间隔≥1s 防封）
                    if attempt:
                        import time as _time
                        _time.sleep(1.2)
                    try:
                        with httpx.Client(timeout=10) as client:
                            resp = client.get(url, params=params, headers=headers)
                            d = resp.json()
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                if d is None:
                    raise RuntimeError(f"东财接口连续失败：{last_exc}")

                items = []
                for line in (d.get("data") or {}).get("klines") or []:
                    parts = line.split(",")
                    if len(parts) >= 6:
                        items.append({
                            "time": parts[0],
                            "main_net": float(parts[1]),
                            "small_net": float(parts[2]),
                            "mid_net": float(parts[3]),
                            "large_net": float(parts[4]),
                            "super_net": float(parts[5]),
                        })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint=f"a-stock-data/{tag}",
                           latency_ms=t["ms"],
                           note=f"资金流向 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"资金流向获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def earnings_forecast(self, code: str = None, limit: int = 50) -> models.DimensionResult:
        """业绩预告（东财数据中心）"""
        try:
            with timed() as t:
                DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params = {
                    "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
                    "columns": "ALL",
                    "pageSize": str(limit),
                    "sortColumns": "NOTICE_DATE",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                }
                if code:
                    code = norm_ticker(code, stock_only=True)
                    params["filter"] = f'(SECURITY_CODE="{code}")'

                with httpx.Client(timeout=30) as client:
                    resp = client.get(DATACENTER_URL, params=params,
                                     headers={"Referer": "https://data.eastmoney.com/"})
                    d = resp.json()
                    data = (d.get("result") or {}).get("data") or []

                items = []
                for row in data[:limit]:
                    items.append({
                        "code": row.get("SECURITY_CODE"),
                        "name": row.get("SECURITY_NAME_ABBR"),
                        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
                        "report_date": str(row.get("REPORT_DATE", ""))[:10],
                        "indicator": row.get("PREDICT_FINANCE"),
                        "forecast_type": row.get("PREDICT_TYPE"),
                        "amount_lower": _v39_num(row.get("PREDICT_AMT_LOWER")),
                        "amount_upper": _v39_num(row.get("PREDICT_AMT_UPPER")),
                        "change_pct_lower": _v39_num(row.get("ADD_AMP_LOWER")),
                        "change_pct_upper": _v39_num(row.get("ADD_AMP_UPPER")),
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/earnings_forecast",
                           latency_ms=t["ms"],
                           note=f"业绩预告 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"业绩预告获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def share_buyback(self, code: str = None, limit: int = 50) -> models.DimensionResult:
        """股票回购（东财数据中心）"""
        try:
            with timed() as t:
                DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params = {
                    "reportName": "RPTA_WEB_GETHGLIST_NEW",
                    "columns": "ALL",
                    "pageSize": str(limit),
                    "sortColumns": "UPD",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                }
                if code:
                    code = norm_ticker(code, stock_only=True)
                    params["filter"] = f'(DIM_SCODE="{code}")'

                with httpx.Client(timeout=30) as client:
                    resp = client.get(DATACENTER_URL, params=params,
                                     headers={"Referer": "https://data.eastmoney.com/"})
                    d = resp.json()
                    data = (d.get("result") or {}).get("data") or []

                items = []
                for row in data[:limit]:
                    items.append({
                        "code": row.get("DIM_SCODE"),
                        "name": row.get("SECURITYSHORTNAME"),
                        "progress": row.get("REPURPROGRESS"),
                        "plan_start": str(row.get("REPURSTARTDATE", ""))[:10],
                        "plan_end": str(row.get("REPURENDDATE", ""))[:10],
                        "price_cap": _v39_num(row.get("REPURPRICECAP")),
                        "shares_lower": _v39_num(row.get("REPURNUMLOWER")),
                        "shares_upper": _v39_num(row.get("REPURNUMCAP")),
                        "done_shares": _v39_num(row.get("REPURNUM")),
                        "done_amount": _v39_num(row.get("REPURAMOUNT")),
                        "latest_notice": str(row.get("UPDATEDATE", ""))[:10],
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/share_buyback",
                           latency_ms=t["ms"],
                           note=f"股票回购 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"股票回购获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def hsgt_realtime(self) -> models.DimensionResult:
        """沪深股通当日实时分钟流向"""
        try:
            with timed() as t:
                url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0",
                    "Referer": "https://data.eastmoney.com/",
                }
                with httpx.Client(timeout=10) as client:
                    resp = client.get(url, headers=headers)
                    d = resp.json()

                times = d.get("time", [])
                hgt = d.get("hgt", [])
                sgt = d.get("sgt", [])
                n = len(times)

                items = []
                for i in range(n):
                    items.append({
                        "time": times[i] if i < len(times) else "",
                        "hgt_yi": float(hgt[i]) if i < len(hgt) else None,
                        "sgt_yi": float(sgt[i]) if i < len(sgt) else None,
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/hsgt_realtime",
                           latency_ms=t["ms"],
                           note=f"北向资金 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"北向资金获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def hot_reason(self, date: str = None) -> models.DimensionResult:
        """同花顺当日强势股归因"""
        try:
            with timed() as t:
                if not date:
                    date = datetime.now().strftime("%Y-%m-%d")

                url = f"http://zx.10jqka.com.cn/event/api/getharden/date/{date}/orderby/date/orderway/desc/charset/GBK/"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0",
                }
                with httpx.Client(timeout=10) as client:
                    resp = client.get(url, headers=headers)
                    data = resp.json()

                if data.get("errocode", 0) != 0:
                    raise RuntimeError(f"同花顺热点错误: {data.get('errormsg', '')}")

                rows = data.get("data") or []
                items = []
                for row in rows[:50]:
                    items.append({
                        "code": row.get("code"),
                        "name": row.get("name"),
                        "reason": row.get("reason"),
                        "close": _v39_num(row.get("close")),
                        "change_pct": _v39_num(row.get("zhangfu")),
                        "turnover": _v39_num(row.get("huanshou")),
                        "amount": _v39_num(row.get("chengjiaoe")),
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=date,
                           endpoint="a-stock-data/hot_reason",
                           latency_ms=t["ms"],
                           note=f"热股归因 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"热股归因获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def institution_survey(self, code: str = None, limit: int = 50) -> models.DimensionResult:
        """机构调研（东财数据中心）"""
        try:
            with timed() as t:
                DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params = {
                    "reportName": "RPT_ORG_SURVEYNEW",
                    "columns": "ALL",
                    "pageSize": str(limit),
                    "sortColumns": "NOTICE_DATE",
                    "sortTypes": "-1",
                    "filter": '(IS_SOURCE="1")(NUMBERNEW="1")',
                    "source": "WEB",
                    "client": "WEB",
                }
                if code:
                    code = norm_ticker(code, stock_only=True)
                    params["filter"] += f'(SECURITY_CODE="{code}")'

                with httpx.Client(timeout=30) as client:
                    resp = client.get(DATACENTER_URL, params=params,
                                     headers={"Referer": "https://data.eastmoney.com/"})
                    d = resp.json()
                    data = (d.get("result") or {}).get("data") or []

                items = []
                for row in data[:limit]:
                    items.append({
                        "code": row.get("SECURITY_CODE"),
                        "name": row.get("SECURITY_NAME_ABBR"),
                        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
                        "survey_date": str(row.get("RECEIVE_START_DATE", ""))[:10],
                        "survey_end": str(row.get("RECEIVE_END_DATE", ""))[:10],
                        "org_count": _v39_num(row.get("SUM")),
                        "survey_way": row.get("RECEIVE_WAY_EXPLAIN"),
                        "place": row.get("RECEIVE_PLACE"),
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/institution_survey",
                           latency_ms=t["ms"],
                           note=f"机构调研 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"机构调研获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def margin_trading(self, code: str, limit: int = 30) -> models.DimensionResult:
        """融资融券明细（日级）—— axdata 东财源优先，失败降级手抄件。"""
        try:
            with timed() as t:
                try:
                    items = _margin_trading_axdata(code, limit=limit)
                    leg = "axdata"
                except Exception:  # noqa: BLE001 — axdata 包/网络失败降级手抄件
                    items = _margin_trading_legacy(code, limit=limit)
                    leg = "legacy"
            return self.ok({"item": items, "total": len(items)},
                           as_of=items[0]["date"] if items else None,
                           endpoint="a-stock-data/margin_trading",
                           latency_ms=t["ms"],
                           note=f"融资融券 {len(items)} 条（{leg}）")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"融资融券获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def block_trade(self, code: str, limit: int = 20) -> models.DimensionResult:
        """大宗交易记录"""
        try:
            with timed() as t:
                code = norm_ticker(code)
                DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params = {
                    "reportName": "RPT_DATA_BLOCKTRADE",
                    "columns": "ALL",
                    "filter": f'(SECURITY_CODE="{code}")',
                    "pageSize": str(limit),
                    "sortColumns": "TRADE_DATE",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                }
                with httpx.Client(timeout=30) as client:
                    resp = client.get(DATACENTER_URL, params=params,
                                     headers={"Referer": "https://data.eastmoney.com/"})
                    d = resp.json()
                    data = (d.get("result") or {}).get("data") or []

                items = []
                for row in data[:limit]:
                    close = _v39_num(row.get("CLOSE_PRICE")) or 0
                    deal_price = _v39_num(row.get("DEAL_PRICE")) or 0
                    premium = ((deal_price / close - 1) * 100) if close else 0
                    items.append({
                        "date": str(row.get("TRADE_DATE", ""))[:10],
                        "price": deal_price,
                        "close": close,
                        "premium_pct": round(premium, 2),
                        "vol": _v39_num(row.get("DEAL_VOLUME")),
                        "amount": _v39_num(row.get("DEAL_AMT")),
                        "buyer": row.get("BUYER_NAME", ""),
                        "seller": row.get("SELLER_NAME", ""),
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=items[0]["date"] if items else None,
                           endpoint="a-stock-data/block_trade",
                           latency_ms=t["ms"],
                           note=f"大宗交易 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"大宗交易获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def holder_trades(self, code: str = None, direction: str = None,
                      limit: int = 50) -> models.DimensionResult:
        """股东增减持（东财数据中心）"""
        try:
            with timed() as t:
                DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params = {
                    "reportName": "RPT_SHARE_HOLDER_INCREASE",
                    "columns": "ALL",
                    "pageSize": str(limit),
                    "sortColumns": "NOTICE_DATE",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                }
                if code:
                    code = norm_ticker(code, stock_only=True)
                    params["filter"] = f'(SECURITY_CODE="{code}")'
                if direction:
                    params["filter"] = params.get("filter", "") + f'(DIRECTION="{direction}")'

                with httpx.Client(timeout=30) as client:
                    resp = client.get(DATACENTER_URL, params=params,
                                     headers={"Referer": "https://data.eastmoney.com/"})
                    d = resp.json()
                    data = (d.get("result") or {}).get("data") or []

                items = []
                for row in data[:limit]:
                    items.append({
                        "code": row.get("SECURITY_CODE"),
                        "name": row.get("SECURITY_NAME_ABBR"),
                        "holder": row.get("HOLDER_NAME"),
                        "direction": row.get("DIRECTION"),
                        "change_shares_10k": _v39_num(row.get("CHANGE_NUM_SYMBOL")),
                        "change_pct_total": _v39_num(row.get("AFTER_CHANGE_RATE")),
                        "after_shares_10k": _v39_num(row.get("AFTER_HOLDER_NUM")),
                        "after_pct_total": _v39_num(row.get("HOLD_RATIO")),
                        "avg_price": _v39_num(row.get("TRADE_AVERAGE_PRICE")),
                        "channel": row.get("MARKET"),
                        "notice_date": str(row.get("NOTICE_DATE", ""))[:10],
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/holder_trades",
                           latency_ms=t["ms"],
                           note=f"股东增减持 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"股东增减持获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def holder_num_change(self, code: str, limit: int = 10) -> models.DimensionResult:
        """股东户数变化（季度级）"""
        try:
            with timed() as t:
                code = norm_ticker(code)
                DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
                params = {
                    "reportName": "RPT_HOLDERNUMLATEST",
                    "columns": "ALL",
                    "filter": f'(SECURITY_CODE="{code}")',
                    "pageSize": str(limit),
                    "sortColumns": "END_DATE",
                    "sortTypes": "-1",
                    "source": "WEB",
                    "client": "WEB",
                }
                with httpx.Client(timeout=30) as client:
                    resp = client.get(DATACENTER_URL, params=params,
                                     headers={"Referer": "https://data.eastmoney.com/"})
                    d = resp.json()
                    data = (d.get("result") or {}).get("data") or []

                items = []
                for row in data[:limit]:
                    items.append({
                        "date": str(row.get("END_DATE", ""))[:10],
                        "holder_num": _v39_num(row.get("HOLDER_NUM")),
                        "change_num": _v39_num(row.get("HOLDER_NUM_CHANGE")),
                        "change_ratio": _v39_num(row.get("HOLDER_NUM_RATIO")),
                        "avg_shares": _v39_num(row.get("AVG_FREE_SHARES")),
                    })

            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/holder_num_change",
                           latency_ms=t["ms"],
                           note=f"股东户数变化 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"股东户数变化获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def tencent_ticks(self, code: str) -> models.DimensionResult:
        """腾讯逐笔成交（当日分笔明细）—— axdata 腾讯源，无降级腿。

        旧手抄件解析分隔符写错（按 `~` 切 `/`+`|` 格式）恒返回 0 条，
        实为死端点（2026-11-01 灰度实测），故本端点不做旧实现降级。
        """
        try:
            with timed() as t:
                prefix = get_prefix(code)
                if prefix == "bj":
                    raise ValueError("腾讯逐笔不支持北交所")
                items = _tencent_ticks_axdata(code)
            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/tencent_ticks",
                           latency_ms=t["ms"],
                           note=f"逐笔成交 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"逐笔成交获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")

    def board_fund_flow(self, board_type: str = "industry",
                        period: str = "today", limit: int = 20) -> models.DimensionResult:
        """板块资金流向排名（东财=备胎口径，2026-10-14 起主源为开盘红 board_flow）。"""
        import time as _time
        br = _breaker(_CLIST_KEY)
        now = _time.monotonic()
        if now < br["open_until"]:
            wait_min = int((br["open_until"] - now) / 60) + 1
            return self.fail(
                models.UNAVAILABLE,
                f"东财板块资金流接口处于 IP 连接级风控（请求被秒拒），熔断冷却中，"
                f"约 {wait_min} 分钟后自动试通——频繁请求会延长封禁，请稍后再看",
                retryable=True, endpoint="a-stock-data/board_fund_flow")
        try:
            with timed() as t:
                if board_type not in ("industry", "concept", "region"):
                    raise ValueError("board_type 须为 industry/concept/region")
                if period not in ("today", "5d", "10d"):
                    raise ValueError("period 须为 today/5d/10d")

                # 板块类型映射
                board_fs = {
                    "industry": "m:90+t:2",  # 行业
                    "concept": "m:90+t:3",   # 概念
                    "region": "m:90+t:1",    # 地域
                }
                # 周期映射
                period_fields = {
                    "today": ("f62", "f184", "f66", "f12"),
                    "5d": ("f164", "f174", "f184", "f12"),
                    "10d": ("f174", "f184", "f184", "f12"),
                }

                url = "https://push2.eastmoney.com/api/qt/clist/get"
                params = {
                    "pn": "1", "pz": str(limit),
                    "po": "1", "np": "1", "fltt": "2", "invt": "2",
                    "fid": "f62",  # 按主力净额排序
                    "fs": board_fs[board_type],
                    "fields": "f12,f14,f62,f184,f66,f72,f78,f84,f3",
                }
                headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://data.eastmoney.com/",
                }
                d = None
                last_exc = None
                for attempt in range(3):  # 东财偶发断连/风控，重试 3 次（间隔≥1.2s 防封）
                    if attempt:
                        _time.sleep(1.2)
                    try:
                        _em_throttle()          # 全局节流：1.0s 基础间隔 + 0.1~0.5s 抖动
                        with httpx.Client(timeout=15) as client:
                            resp = client.get(url, params=params, headers=headers)
                            d = resp.json().get("data") or {}
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                if d is None:
                    br["consec_fail"] += 1
                    if br["consec_fail"] >= 2:
                        idx = min(br["consec_fail"] - 2,
                                  len(_BOARD_FF_COOLDOWNS) - 1)
                        br["open_until"] = now + _BOARD_FF_COOLDOWNS[idx]
                        _alert_source_risk(
                            _CLIST_KEY, "fail",
                            f"连续 {br['consec_fail']} 轮请求被拒，冷却 "
                            f"{(_BOARD_FF_COOLDOWNS[idx] + 59) // 60} 分钟"
                            f"（11-07 拍板：开闸即提醒）")
                    raise RuntimeError(f"东财板块资金流接口连续失败：{last_exc}")

                items = []
                for i, it in enumerate(d.get("diff") or []):
                    items.append({
                        "rank": i + 1,
                        "name": it.get("f14", ""),
                        "code": it.get("f12", ""),
                        "change_pct": _v39_num(it.get("f3")),
                        "main_net": _v39_num(it.get("f62")),
                        "main_pct": _v39_num(it.get("f184")),
                        "super_large_net": _v39_num(it.get("f66")),
                        "large_net": _v39_num(it.get("f72")),
                        "medium_net": _v39_num(it.get("f78")),
                        "small_net": _v39_num(it.get("f84")),
                    })

            if br["open_until"] > 0.0:
                _alert_source_risk(_CLIST_KEY, "recovered", "")  # 曾开闸：知会恢复
            br["consec_fail"] = 0
            br["open_until"] = 0.0
            return self.ok({"item": items, "total": len(items)},
                           as_of=datetime.now().strftime("%Y-%m-%d"),
                           endpoint="a-stock-data/board_fund_flow",
                           latency_ms=t["ms"],
                           note=f"板块资金流向 {len(items)} 条")
        except Exception as e:
            return self.fail(models.UNAVAILABLE, f"板块资金流向获取失败：{e}",
                             retryable=True, endpoint="a-stock-data")
