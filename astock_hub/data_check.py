# -*- coding: utf-8 -*-
"""每日数据体检（2026-11-21 立项，盘前 08:50 计划任务 astock-data-check）。

把散落的"静默失败"变成每天一条飞书汇总：每个数据源 1 发轻量活体探针 +
本地状态（落库/qfq/框架/对账/日历）检查 + 昨收跨源抽查，全部项无论好坏
每天一条消息（全过=简短确认，缺消息=体检本身坏了）。

- 交易日闸门：周末本地时钟权威优先（weekday>=5 静默，压过日历新鲜度
  规则——11-14/11-15 周末误报教训），非交易日沿用 chain_guard._is_trading_day
  （宁误报不漏报）。
- 告警走 chain_guard 通道，链名 data-check，同日防重一条；--test 不占
  防重键、周末也可发（手动验证用）。
- 结果落档 data/data_check/YYYY-MM-DD.json（原子写）。
- 每个探针独立 try：一个源炸了不拖垮整轮，异常记为该项链的 fail。
- 东财 push2 不重复探测——11-07 已有熔断开闸实时告警（data-src），
  体检里记为"跳过"项自文档化。

守望类惯例：main 恒 exit 0（--test 发送失败除外），任何自身异常不许外抛。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

from . import freshness as fz
from . import models

CHAIN = "data-check"
PROBE_SYMBOL = "600519"          # 跨源抽查锚点（贵州茅台，与腾讯探针同一票）
_PROBE_CODE = "600519.SH"
_PROBE_THSCODE = "600519.SH"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = _PROJECT_ROOT / "data" / "data_check"
FRAMEWORK_LATEST = _PROJECT_ROOT / "data" / "framework_report_latest.json"
RECONCILE_LATEST = _PROJECT_ROOT / "data" / "reconcile_latest.json"
DIGEST_POOL = _PROJECT_ROOT / "data" / "digest_pool.json"

_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_CROSS_TOL = 0.01                # 昨收交叉核对容差（±1 分）
_DIGEST_MAX_AGE = 3              # 纪要池允许的停摆交易日数（周末/无帖日）
_STORE_MAX_AGE = 2               # 无日历时本地库允许的最大交易日龄
_CAL_OK_GAP = 3                  # 日历最新日期距今天 ≤3 天属正常（周末回看窗口）


# ============================================================
# 条目与汇总（纯函数）
# ============================================================

def _item(item_id: str, status: str, label: str, detail: str,
          hint: str | None = None) -> dict:
    it = {"id": item_id, "label": label, "status": status, "detail": detail}
    if hint:
        it["hint"] = hint
    return it


def _summarize(items: list[dict]) -> dict:
    out = {"ok": 0, "warn": 0, "fail": 0, "skipped": 0}
    for i in items:
        out[i.get("status", "fail")] = out.get(i.get("status", "fail"), 0) + 1
    return out


def _derive_prev_td(days, today_str: str) -> tuple[str | None, int]:
    """(上一交易日, 日历最新日期距今天的天数)。

    hithink 日历是回看窗口（刷新日截止、不含未来），盘前 max=昨天属正常
    （gap≤3，含周末）；gap≥4 才是真滞后（10-26 日历滞后一周教训）。
    prev_td 取日历里今天之前的最大日期——只要日历含全部已知交易日，
    它就是上一交易日（滞后时可能低估一天，日历项会 warn 标出）。
    """
    if not days:
        return None, 0
    norm = sorted({str(d)[:10] for d in days})
    prevs = [d for d in norm if d < today_str]
    prev_td = prevs[-1] if prevs else None
    try:
        gap = (date.fromisoformat(today_str)
               - date.fromisoformat(norm[-1])).days
    except ValueError:
        gap = 99
    return prev_td, max(gap, 0)


def _age_ok(day_str: str | None, today_str: str, max_age: int) -> bool | None:
    """新鲜度兜底：day 无法解析返回 None（按不通过处理由调用方决定）。"""
    d = fz.parse_as_of(day_str)
    if d is None:
        return None
    return fz.trading_age_days(d, date.fromisoformat(today_str)) <= max_age


def _local_store_item(raw_max: str | None, prev_td: str | None,
                      today_str: str) -> dict:
    if not raw_max:
        return _item(
            "local_store", "fail", "本地行情库",
            "raw 主库读不到或缺失",
            hint="data/market/kline_daily_raw/_all.parquet 应存在；看 16:00 链日志")
    if prev_td:
        if raw_max >= prev_td:
            return _item("local_store", "ok", "本地行情库", f"最新 {raw_max}")
        return _item(
            "local_store", "fail", "本地行情库",
            f"最新 {raw_max}，应到 {prev_td}（落后，昨日链可能失败）",
            hint="看 data/market-1600.log 与 17:45 兜底链（data/chain_status/）")
    ok = _age_ok(raw_max, today_str, _STORE_MAX_AGE)
    if ok:
        return _item("local_store", "ok", "本地行情库",
                     f"最新 {raw_max}（日历滞后，按新鲜度判定）")
    return _item(
        "local_store", "fail", "本地行情库",
        f"最新 {raw_max}，日历不可用且落后超过 {_STORE_MAX_AGE} 个交易日",
        hint="看 data/market-1600.log 与 17:45 兜底链")


def _qfq_sync_item(raw_max: str | None, qfq_max: str | None) -> dict:
    if not qfq_max:
        return _item(
            "qfq_sync", "fail", "前复权库同步",
            "qfq 库缺失",
            hint="先跑 scripts/build_kline_qfq.py；此后由 sync_store 每日增量")
    if not raw_max:
        return _item("qfq_sync", "fail", "前复权库同步",
                     "raw 库读不到（见本地行情库项）")
    if qfq_max >= raw_max:
        return _item("qfq_sync", "ok", "前复权库同步", f"qfq=raw（{qfq_max}）")
    return _item(
        "qfq_sync", "fail", "前复权库同步",
        f"qfq 断更：停在 {qfq_max}，raw 已到 {raw_max}（11-19 假跌停同款根因）",
        hint="sync_store 应调 refresh_qfq_daily；断更期间复权价失真，涨跌停判定禁用 qfq")


def _framework_item(d: dict | None, prev_td: str | None,
                    today_str: str) -> dict:
    if not d:
        return _item(
            "framework", "fail", "框架报告",
            "framework_report_latest.json 缺失或损坏",
            hint="16:00 链产出、17:45 兜底重建；连续缺失看 data/chain_status/")
    asof = str(d.get("asof") or "")
    if not asof:
        return _item("framework", "fail", "框架报告", "报告无 asof 字段")
    if prev_td:
        if asof >= prev_td:
            return _item("framework", "ok", "框架报告", f"基准日 {asof}")
        return _item(
            "framework", "fail", "框架报告",
            f"基准日 {asof}，应到 {prev_td}（昨日链未重建）",
            hint="17:45 兜底链 astock-market-1745 应已重跑；看 data/chain_status/")
    ok = _age_ok(asof, today_str, _STORE_MAX_AGE)
    if ok:
        return _item("framework", "ok", "框架报告", f"基准日 {asof}")
    return _item("framework", "fail", "框架报告",
                 f"基准日 {asof}（日历不可用且已陈旧）")


def _reconcile_item(d: dict | None, prev_td: str | None,
                    today_str: str) -> dict:
    if not d:
        return _item(
            "reconcile", "warn", "收盘对账",
            "无对账档（data/reconcile_latest.json 不存在）",
            hint="16:00 链 gate 步产出；首次上线前无档属正常")
    asof = str(d.get("as_of") or "")
    passed = d.get("passed")
    if passed is False:
        return _item(
            "reconcile", "fail", "收盘对账",
            f"对账 FAIL（{asof}）——涨跌停验真未过",
            hint="详见 data/reconcile_latest.json 的 checks 明细")
    if passed is None or d.get("ref_unavailable"):
        return _item(
            "reconcile", "warn", "收盘对账",
            f"参考源不可用降级跳过（{asof}）——非本地数据错误（GATE DEGRADED 语义）")
    if prev_td and asof < prev_td:
        return _item("reconcile", "warn", "收盘对账",
                     f"对账档陈旧（{asof}，应到 {prev_td}）")
    return _item("reconcile", "ok", "收盘对账", f"PASS（{asof}）")


def _cross_item(prev_close, price, local_close) -> dict:
    """腾讯昨收/现价任一与本地吻合即通过。

    周末腾讯 prev_close 停在周四（现价才是周五收盘）、盘中 prev_close 才是
    上一交易日——两个字段各管一段场景，任一命中即证本地与腾讯同源一致；
    两者都不中（除权日/真口径差）才提示人工核对。
    """
    if local_close is None:
        return _item("cross", "skipped", "昨收交叉核对",
                     "本地库缺该股上一交易日收盘")
    try:
        local = float(local_close)
    except (TypeError, ValueError):
        return _item("cross", "skipped", "昨收交叉核对", "本地数值无法解析")
    for field, val in (("昨收", prev_close), ("现价", price)):
        if val is None:
            continue
        try:
            diff = abs(float(val) - local)
        except (TypeError, ValueError):
            continue
        if diff <= _CROSS_TOL:
            return _item("cross", "ok", "昨收交叉核对",
                         f"腾讯{field} {val} = 本地 {local_close}")
    if prev_close is None and price is None:
        return _item("cross", "skipped", "昨收交叉核对",
                     "腾讯探针未取到价格字段")
    return _item(
        "cross", "warn", "昨收交叉核对",
        f"腾讯昨收 {prev_close} / 现价 {price} 均不等于本地 {local_close}"
        f"（可能除权或口径差，人工核对）")


def _digest_item(pool: dict | None, today_str: str) -> dict:
    if not pool:
        return _item(
            "digest", "warn", "纪要股票池",
            "纪要池文件缺失或损坏",
            hint="data/digest_pool.json（0:00/12:00 纪要任务产出）")
    stocks = pool.get("stocks") or {}
    lasts = [str(s.get("last_seen")) for s in stocks.values()
             if isinstance(s, dict) and s.get("last_seen")]
    if not lasts:
        return _item("digest", "warn", "纪要股票池", "池内没有任何个股")
    last = max(lasts)
    d = fz.parse_as_of(last)
    age = fz.trading_age_days(d, date.fromisoformat(today_str)) if d else None
    if age is None:
        return _item("digest", "warn", "纪要股票池", f"last_seen 无法解析：{last}")
    if age <= _DIGEST_MAX_AGE:
        return _item("digest", "ok", "纪要股票池", f"最新提及 {last}")
    return _item("digest", "warn", "纪要股票池",
                 f"疑似停更：最新提及 {last}（已 {age} 个交易日）",
                 hint="看 0:00/12:00 纪要抓取任务与 AI 汇总")


# ============================================================
# IO 探针（每个独立 try；异常由 gather 记为该项链的 fail）
# ============================================================

def _probe_calendar(cfg: dict, today: date) -> tuple[dict, str | None]:
    from .store import market_data as md
    days = md.calendar_days(cfg)
    if not days:
        return _item("calendar", "fail", "交易日历", "日历为空"), None
    prev_td, gap = _derive_prev_td(days, today.isoformat())
    newest = max(str(d)[:10] for d in days)
    if gap <= _CAL_OK_GAP:
        return _item("calendar", "ok", "交易日历", f"最新 {newest}"), prev_td
    return (_item("calendar", "warn", "交易日历",
                  f"日历滞后 {gap} 天（最新 {newest}），今天按宁误报放行，"
                  f"上一交易日可能低估",
                  hint="16:00 链日历先行步会强制刷新；持续滞后先查该步"),
            prev_td)


def _store_files(cfg: dict) -> tuple[Path, Path]:
    from .store import parquet_store
    store = parquet_store._store_dir(cfg)
    return (store / "kline_daily_raw" / "_all.parquet",
            store / "kline_daily" / "_all.parquet")


def _scan_max_date(path: Path) -> str | None:
    if not path.exists():
        return None
    import polars as pl
    # date 列可能是 Date dtype 也可能是字符串（10-24 教训），统一转串再取 max
    frame = (pl.scan_parquet(str(path))
             .select(pl.col("date").cast(pl.Utf8).str.slice(0, 10).max())
             .collect())
    val = frame.to_series()[0]
    return str(val)[:10] if val else None


def _load_raw_max(cfg: dict) -> str | None:
    return _scan_max_date(_store_files(cfg)[0])


def _load_qfq_max(cfg: dict) -> str | None:
    return _scan_max_date(_store_files(cfg)[1])


def _load_symbol_close(cfg: dict, symbol: str, day: str) -> float | None:
    if not day:
        return None
    raw, _ = _store_files(cfg)
    if not raw.exists():
        return None
    import polars as pl
    frame = (pl.scan_parquet(str(raw))
             .select([pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("_d"),
                      pl.col("symbol").cast(pl.Utf8).alias("_s"),
                      pl.col("close")])
             .filter((pl.col("_s") == symbol) & (pl.col("_d") == day))
             .collect())
    if frame.height == 0:
        return None
    return float(frame["close"][-1])


def _probe_axdata(cfg: dict) -> dict:
    from .adapters.astock_data import _axdata_client, get_prefix, norm_ticker
    sym = get_prefix(_PROBE_CODE) + norm_ticker(_PROBE_CODE)
    pdf = _axdata_client().call("stock_realtime_snapshot_tdx", code=sym)
    n = 0 if pdf is None else len(pdf)
    if n <= 0:
        return _item(
            "axdata", "fail", "axdata 通达信协议", "单票快照空返回",
            hint="通达信协议栈可用性（7709）；K线主源连续失败会拖累盘后落库")
    return _item("axdata", "ok", "axdata 通达信协议", f"单票快照 {n} 行")


def _probe_tencent(cfg: dict) -> tuple[dict, tuple[float | None, float | None]]:
    from .adapters.tencent import TencentAdapter
    r = TencentAdapter(cfg.get("tencent") or {}).snapshot([_PROBE_CODE])
    if r.status not in (models.OK, models.STALE):
        msg = (r.error or {}).get("message") or r.status
        return _item("tencent", "fail", "腾讯行情", str(msg)[:120],
                     hint="实时快照/指数/分时备胎都走腾讯，失败影响面大"), (None, None)
    items = (r.data or {}).get("item") or []
    if not items:
        return _item("tencent", "fail", "腾讯行情", "快照空返回"), (None, None)
    it = items[0]
    return (_item("tencent", "ok", "腾讯行情",
                  f"{it.get('name') or ''} 快照 {len(items)} 条".strip()),
            (it.get("prev_close"), it.get("price")))


def _probe_kph(cfg: dict) -> dict:
    from . import kph_client
    m = kph_client.market_emotion()
    if not m or m.get("up") is None:
        return _item(
            "kph", "fail", "开盘红情绪", "市场情绪空返回",
            hint="开盘红 App 接口可能变更/限流；对账参考会降级（GATE DEGRADED）")
    return _item("kph", "ok", "开盘红情绪",
                 f"涨 {m.get('up')} 跌 {m.get('down')} 涨停 {m.get('limit_up')}")


def _probe_hithink(cfg: dict) -> dict:
    from .adapters.hithink import HithinkAdapter
    r = HithinkAdapter(cfg.get("hithink") or {}).health()
    if r.status in (models.OK, models.STALE):
        return _item("hithink", "ok", "hithink 同花顺", "CLI 可用")
    msg = (r.error or {}).get("message") or r.status
    return _item("hithink", "fail", "hithink 同花顺", str(msg)[:120],
                 hint="hithink-finance CLI 安装/额度；K线兜底与估值核实受影响")


def _probe_zsxq(cfg: dict, today: date) -> dict:
    from .adapters.zsxq import ZsxqAdapter
    r = ZsxqAdapter(cfg.get("zsxq") or {}).health()
    if r.status not in (models.OK, models.STALE):
        msg = (r.error or {}).get("message") or r.status
        return _item("zsxq", "fail", "星球纪要日报", str(msg)[:120],
                     hint="0:00/12:00 抓取与 AI 汇总链（zsxq-cli 授权）")
    as_of = r.meta.as_of
    if fz.assess(as_of, "daily", today).get("stale"):
        return _item("zsxq", "warn", "星球纪要日报",
                     f"最新 {as_of}（偏旧，日报链可能中断）")
    return _item("zsxq", "ok", "星球纪要日报", f"最新 {as_of}")


def _probe_news(cfg: dict, today: date) -> dict:
    from .adapters.investment_news import InvestmentNewsAdapter
    r = InvestmentNewsAdapter(cfg.get("investment_news") or {}).health()
    if r.status not in (models.OK, models.STALE):
        msg = (r.error or {}).get("message") or r.status
        return _item("news", "fail", "产业资讯", str(msg)[:120],
                     hint="0:00 资讯链（news_pipe 抓取+翻译）")
    as_of = r.meta.as_of
    if fz.assess(as_of, "daily", today).get("stale"):
        return _item("news", "warn", "产业资讯",
                     f"最新 {as_of}（偏旧，资讯链可能中断）")
    return _item("news", "ok", "产业资讯", f"最新 {as_of}")


def _probe_digest(today: date) -> dict:
    pool = None
    if DIGEST_POOL.exists():
        try:
            pool = json.loads(DIGEST_POOL.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pool = None
    return _digest_item(pool, today.isoformat())


def _probe_framework(prev_td: str | None, today: date) -> dict:
    d = None
    if FRAMEWORK_LATEST.exists():
        try:
            d = json.loads(FRAMEWORK_LATEST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = None
    return _framework_item(d, prev_td, today.isoformat())


def _probe_reconcile(prev_td: str | None, today: date) -> dict:
    d = None
    if RECONCILE_LATEST.exists():
        try:
            d = json.loads(RECONCILE_LATEST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = None
    return _reconcile_item(d, prev_td, today.isoformat())


# ============================================================
# 汇总编排
# ============================================================

def _try(fn):
    try:
        return fn(), None
    except Exception as e:  # noqa: BLE001 —— 单项读取失败不许拖垮整轮
        return None, f"{type(e).__name__}: {str(e)[:150]}"


def _collect(items: list[dict], item_id: str, label: str, fn, *args, **kwargs):
    """执行探针并登记条目；探针异常 → 该项链的 fail 条目。

    返回 (item, extra)：探针可返回 item 或 (item, extra)（如腾讯的昨收）。
    """
    try:
        res = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 —— 探针隔离铁律
        it = _item(item_id, "fail", label,
                   f"探针异常：{type(e).__name__}: {str(e)[:150]}")
        items.append(it)
        return it, None
    if isinstance(res, tuple):
        it, extra = res
    else:
        it, extra = res, None
    items.append(it)
    return it, extra


def gather(cfg: dict, today: date) -> dict:
    """跑全部探针，返回 {date, prev_td, items, summary}。"""
    items: list[dict] = []

    _, prev_td = _collect(items, "calendar", "交易日历",
                          _probe_calendar, cfg, today)

    raw_max, raw_err = _try(lambda: _load_raw_max(cfg))
    qfq_max, _qfq_err = _try(lambda: _load_qfq_max(cfg))

    if raw_err:
        items.append(_item("local_store", "fail", "本地行情库",
                           f"读取失败：{raw_err}"))
    else:
        items.append(_local_store_item(raw_max, prev_td, today.isoformat()))
    items.append(_qfq_sync_item(raw_max, qfq_max))

    _collect(items, "framework", "框架报告", _probe_framework, prev_td, today)
    _collect(items, "reconcile", "收盘对账", _probe_reconcile, prev_td, today)

    _collect(items, "axdata", "axdata 通达信协议", _probe_axdata, cfg)
    _, tencent_prices = _collect(items, "tencent", "腾讯行情",
                                 _probe_tencent, cfg)
    _collect(items, "kph", "开盘红情绪", _probe_kph, cfg)
    _collect(items, "hithink", "hithink 同花顺", _probe_hithink, cfg)
    _collect(items, "zsxq", "星球纪要日报", _probe_zsxq, cfg, today)
    _collect(items, "news", "产业资讯", _probe_news, cfg, today)
    _collect(items, "digest", "纪要股票池", _probe_digest, today)

    local_close, _close_err = _try(
        lambda: _load_symbol_close(cfg, _PROBE_THSCODE, prev_td or ""))
    t_prev, t_price = tencent_prices or (None, None)
    items.append(_cross_item(t_prev, t_price, local_close))

    # 东财不重复探测：11-07 起熔断开闸即发 data-src 实时告警，此处只留痕
    items.append(_item(
        "eastmoney", "skipped", "东财 push2",
        "不重复探测——已有数据源风控实时告警（data-src）覆盖"))

    return {"date": today.isoformat(), "prev_td": prev_td, "items": items,
            "summary": _summarize(items)}


def build_message(report: dict) -> str:
    """飞书 markdown 卡片（不支持 #/表格/引用，只用加粗+列表）。"""
    d = date.fromisoformat(report["date"])
    s = report["summary"]
    items = report["items"]
    lines = [f"🩺 **每日数据体检** {report['date']}（{_WD[d.weekday()]}）"]

    if s["fail"] == 0 and s["warn"] == 0:
        head = f"**结果**：✅ 全部 {s['ok']} 项通过"
        if s["skipped"]:
            head += f" · ⏭ {s['skipped']} 跳过"
        lines.append(head)
    else:
        parts = [f"✅ {s['ok']} 通过"]
        if s["warn"]:
            parts.append(f"⚠️ {s['warn']} 关注")
        if s["fail"]:
            parts.append(f"🔴 {s['fail']} 失败")
        if s["skipped"]:
            parts.append(f"⏭ {s['skipped']} 跳过")
        lines.append("**结果**：" + " · ".join(parts))

    def sec(title: str, status: str, with_hint: bool = False) -> None:
        sel = [i for i in items if i["status"] == status]
        if not sel:
            return
        lines.append("")
        lines.append(f"**{title}**")
        for i in sel:
            lines.append(f"• **{i['label']}**：{i['detail']}")
            if with_hint and i.get("hint"):
                lines.append(f"　↳ {i['hint']}")

    sec("🔴 失败（需处理）", "fail", with_hint=True)
    sec("⚠️ 关注", "warn")

    ok_items = [i for i in items if i["status"] == "ok"]
    if ok_items:
        lines.append("")
        lines.append("**✅ 通过**")
        lines.append("• " + " · ".join(i["label"] for i in ok_items))

    sec("⏭ 跳过", "skipped")
    return "\n".join(lines)


def _write_report(day: str, report: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    final = REPORT_DIR / f"{day}.json"
    tmp = REPORT_DIR / f".{day}.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, final)


# ============================================================
# CLI（计划任务入口：python -m astock_hub.data_check）
# ============================================================

def main(argv=None, now: datetime | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")   # GBK 控制台防 emoji 崩
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        prog="astock_hub.data_check",
        description="每日数据体检（盘前探针 + 飞书汇总）")
    ap.add_argument("--test", action="store_true",
                    help="手动测试：任何日子都跑、发送不占防重键")
    ap.add_argument("--no-alert", action="store_true", help="只落档不发消息")
    ap.add_argument("--force", action="store_true", help="忽略当日防重")
    ap.add_argument("--json", action="store_true", help="报告输出为 JSON")
    args = ap.parse_args(argv)

    now = now or datetime.now()
    day = now.date().isoformat()

    from .config import load_config
    cfg = load_config()
    if (cfg.get("data_check") or {}).get("enabled", True) is False:
        print("data_check.enabled=false，体检停用")
        return 0

    if not args.test:
        if now.weekday() >= 5:
            # 本地时钟权威（压过日历新鲜度规则）——周末绝不打扰
            print("周末静默（本地时钟权威）")
            return 0
        from . import chain_guard as cg
        if not cg._is_trading_day(cfg, day):
            print("非交易日静默")
            return 0

    report = gather(cfg, now.date())
    try:
        _write_report(day, report)
    except Exception as e:  # noqa: BLE001 —— 落档失败不拦发送
        print(f"报告落档失败（忽略）：{e}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        s = report["summary"]
        print(f"data-check {day}: ok={s['ok']} warn={s['warn']} "
              f"fail={s['fail']} skipped={s['skipped']}")

    if args.no_alert:
        print("未发消息（--no-alert）")
        return 0

    msg = build_message(report)
    from . import chain_guard as cg
    ok, reason = cg.send_alert_detailed(
        day, CHAIN, msg, dedup=not (args.test or args.force))
    if ok:
        print("体检消息已发送")
    else:
        print(f"未发送：{reason}")
    if args.test and not ok:
        return 1          # --test 是唯一允许非 0 的路径（同 engine_watchdog）
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
