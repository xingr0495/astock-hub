# -*- coding: utf-8 -*-
"""国家队宽基 ETF 份额「当日增持」监控（2026-10-27 接入面板；同日升级真日频）。

数据源优先级：
1. **真日频**（首选）：`etf_json_daily_path`（scripts/nt_etf_daily_fetch.py 用
   ak.fund_etf_scale_sse 逐日回填的真实份额），当日变化 = T 日份额 − T−1 日份额；
2. 周内均摊（回落）：日频文件缺失时，把周频披露的周净申购按交易日均摊——
   周总量真实、日分布是估计。

通用口径：
- 当天变化 > 0 即视为"国家队当天在增持"；
- 非交易日不产生行（周末剔除、节假日按交易日历剔除，日历缺失时仅按周末剔）；
- 日频文件也只含已发布日期（当日份额次日才发布，当天显示最近有数日）。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from astock_hub.config import PROJECT_ROOT, load_config

WEEKLY_SRC = ""  # 旧本机默认已废：周频源由 [national_team].etf_json_path 配置
NOTE_DAILY = "上交所逐日披露的真实份额（当日变化=T日−T−1日）；非交易日不显示。"
NOTE_INTERP = ("周频披露按交易日均摊的估计值（非真实逐日值）；"
               "数据到最新完整披露周，非交易日不显示。")


def _resolve_cfg_path(value: str) -> Path | None:
    """配置路径：空→None；绝对直接用；相对按项目根解析（云上 data/... 零配置）。"""
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _weekly_rows() -> list[dict]:
    cfg = load_config()
    path = _resolve_cfg_path(
        (cfg.get("national_team") or {}).get("etf_json_path") or WEEKLY_SRC)
    if path is None or not path.is_file():
        return []
    # 周频文件由「国家队持仓估计」项目产出，历史上是 GBK（2026-10-29 实测
    # position 文件首字节 0xc9 为 GB 编码），日频文件已是 UTF-8——两个文件
    # 编码不同步。此前硬按 UTF-8 读直接抛 UnicodeDecodeError，把整个
    # monitor_summary 打挂，看板显示「份额数据不可用」（日频数据其实完好）。
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            rows = json.loads(raw.decode(enc))
            return sorted(rows, key=lambda r: r["date"])
        except (UnicodeDecodeError, ValueError, KeyError):
            continue
    return []


def _daily_rows() -> list[dict]:
    """真日频份额文件（nt_etf_daily_fetch.py 产出）；缺失返回空。"""
    cfg = load_config()
    path = _resolve_cfg_path(
        (cfg.get("national_team") or {}).get("etf_json_daily_path") or "")
    if path is None or not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return sorted((r for r in rows if r.get("全宽基合计")),
                  key=lambda r: r["date"])


def _load_daily_real() -> pd.DataFrame:
    """真日频：相邻两日份额差 → 单日变化（亿份）。"""
    rows = _daily_rows()
    if len(rows) < 2:
        return pd.DataFrame(columns=["date", "单日变化_亿份", "累计进度_pct"])
    recs = []
    prev_total = None
    for r in rows:
        total = r["全宽基合计"]
        if prev_total is not None:
            d = r["date"]
            recs.append({
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "单日变化_亿份": total - prev_total,
                "累计进度_pct": None,
            })
        prev_total = total
    return pd.DataFrame(recs)


def _trading_days() -> set[str]:
    """交易日集合（YYYY-MM-DD）。日历缺失/滞后时退化为"非周末即交易日"。"""
    try:
        from astock_hub.store.market_data import calendar_days
        return set(calendar_days(load_config()))
    except Exception:  # noqa: BLE001
        return set()


def load_daily() -> pd.DataFrame:
    """单日份额变化（亿份）。列：date / 单日变化_亿份 / 累计进度_pct。

    优先真日频文件；缺失时回落周内均摊估计。
    """
    real = _load_daily_real()
    if not real.empty:
        return real
    rows = _weekly_rows()
    if len(rows) < 2:
        return pd.DataFrame(columns=["date", "单日变化_亿份", "累计进度_pct"])
    tdays = _trading_days()
    recs = []
    for i in range(1, len(rows)):
        pd_, cd_ = pd.Timestamp(rows[i - 1]["date"]), pd.Timestamp(rows[i]["date"])
        prev_t, cur_t = rows[i - 1].get("全宽基合计"), rows[i].get("全宽基合计")
        if not prev_t or not cur_t:
            continue
        # 该周交易日：自然日跨度内；有日历则严格按日历，否则剔周末
        span_days = int((cd_ - pd_).days)
        if span_days <= 0 or span_days > 20:
            continue
        if tdays:
            days = sorted(d for d in tdays
                          if pd_ < pd.Timestamp(d) <= cd_)
            days = [pd.Timestamp(d) for d in days]
        else:
            days = [pd_ + pd.Timedelta(days=k) for k in range(1, span_days + 1)
                    if (pd_ + pd.Timedelta(days=k)).weekday() < 5]
        n = len(days)
        if n == 0:
            continue
        total = cur_t - prev_t
        for k, d in enumerate(days, start=1):
            recs.append({"date": d.strftime("%Y-%m-%d"),
                         "单日变化_亿份": total / n,
                         "累计进度_pct": (cur_t / prev_t - 1) * 100 * k / n})
    df = pd.DataFrame(recs)
    return df.dropna(subset=["单日变化_亿份"]) if not df.empty else df


def monitor_summary(days: int = 10) -> dict:
    """面板接口用的摘要（不含 DataFrame 类型）。"""
    df = load_daily()
    source = "daily" if not _load_daily_real().empty else "interpolated"
    note = NOTE_DAILY if source == "daily" else NOTE_INTERP
    if df.empty:
        return {"status": "unavailable", "note": note,
                "error": "国家队 ETF 份额数据不可用（文件缺失或为空）"}
    recent = df.tail(max(1, days))
    last = df.iloc[-1]
    v = float(last["单日变化_亿份"])
    # 周频只是补充展示（周涨幅/累计），缺失或读坏不应拖垮整个卡片
    try:
        weekly = _weekly_rows()
    except Exception:  # noqa: BLE001 — 周频源损坏时如实降级，日频仍出
        weekly = []
    last_week = weekly[-1] if weekly else None
    return {
        "status": "ok",
        "source": source,
        "as_of": str(last["date"]),
        "weekly_as_of": last_week["date"] if last_week else None,
        "latest": {
            "date": str(last["date"]),
            "daily_change_yi": round(v, 3),
            "state": "增持" if v > 0 else ("减持" if v < 0 else "持平"),
        },
        "series": [
            {"date": str(r["date"]),
             "daily_change_yi": round(float(r["单日变化_亿份"]), 3),
             "state": "增持" if r["单日变化_亿份"] > 0 else "减持"}
            for r in recent.to_dict("records")
        ],
        "weekly_latest": ({
            "date": last_week["date"],
            "wchg_pct": round(
                (last_week["全宽基合计"] / weekly[-2]["全宽基合计"] - 1) * 100, 2)
            if len(weekly) >= 2 else None,
            "total_yi": last_week.get("全宽基合计"),
        } if last_week else None),
        "note": note,
    }


def nt_accumulation(days: int = 5) -> dict:
    """近 N 个交易日宽基 ETF 净申购（亿份）——冷市独立买入信号（用户拍板 2026-11-18）。

    数据为 T 日份额次日发布，最近一日可能是空窗（返回里带 latest_date）。
    increasing=True 表示窗口内累计净申购为正（国家队在增持）。
    """
    real = _load_daily_real()
    if real.empty:
        return {"increasing": None, "reason": "日频数据缺失（未回填）"}
    tail = real.tail(days)
    net = float(tail["单日变化_亿份"].sum())
    last_date = str(tail["date"].iloc[-1])
    last_delta = float(tail["单日变化_亿份"].iloc[-1])
    return {
        "increasing": net > 0,
        "net": round(net, 1),
        "days": int(len(tail)),
        "latest": last_delta,
        "latest_date": last_date,
        "note": f"近{len(tail)}交易日累计净申购 {net:+.1f} 亿份（{last_date} 当日 {last_delta:+.1f}）",
    }
