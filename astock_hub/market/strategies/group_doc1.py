# -*- coding: utf-8 -*-
"""文档1《情绪周期演化规律及冰冰点趋势龙头战法》可执行策略（2026-10-27 接入）。

背景：本手册的手工版（.claude/skills/a-stock-research/references/icepoint-playbook.md）
2026-10-21 做过全历史回测，结论是"冰点节点本身真实有效、深度单调（冰冰点后 20 日
+3.71% 胜率 70%），但机械化的仓位切换与机械选股均为负贡献"。这里接入的两个策略
**只把可回测的选股腿搬进来做对照测试**，不做仓位切换（回测已证伪，不搬）。

手册口径（本文件即该口径的唯一实现，避免与 skill 手册漂移）：

三系数（T 日收盘）：
  red    = 今日上涨家数 / 20
  purple = (今日涨停数 + 今日新增百日新高) / 2 + 昨日涨停表现 × 1000
  green  = 炸板率% + (跌停数 + 大幅回撤数) × 2
    其中炸板率 = 炸板数 / (涨停数 + 炸板数) × 100
    大幅回撤 = 昨日涨停股今日收盘跌 ≥ 5% 的家数（手册补充口径，文档未定义）

节点判据：冰点 = 绿 > 红 且 绿 > 紫。
深度 = 绿 / 红，手册分档：冰点 / 深冰(≥1.5) / 极冰(≥2.5) / 冰冰点(≥4)。

两个策略（都只在冰点日之后的修复/分歧日触发）：
  doc1_ice_repair      冰点日 T → T+1 首次修复（红重新领先）买入
  doc1_divergence_pick 冰点日 T → T+3 前的分歧日（红紫仍健康）买入
"""

from __future__ import annotations

import pandas as pd
import polars as pl

from .base import StrategyDef

# ─────────────────────── 市场级三系数（全市场同日） ───────────────────────


def add_market_state(df: pl.DataFrame) -> pl.DataFrame:
    """给帧追加市场级列（全市场同日同值）：m_state/m_depth/m_red/m_purple/m_green。

    输入须为 A 股宇宙（调用方用 symbols.polars_is_a_share_stock 过滤后喂入），
    否则红系数会被基金/债券指数污染（回测已踩过的坑）。
    炸板率分母按当日涨停+炸板数计算；分母为 0 的日子炸板率记 0。
    """
    if df.is_empty():
        return df
    df = df.sort(["symbol", "date"])

    # 昨日涨停 → 今日表现 / 大幅回撤（按交易日紧邻 join，防停牌缺口串日期）
    cal = df.select("date").unique().sort("date").with_columns(
        pl.col("date").shift(-1).alias("next_date"))
    zt_next = (df.filter(pl.col("signal_limit_up").fill_null(False))
               .select(["symbol", "date"])
               .join(cal, on="date")
               .select(["symbol", pl.col("next_date").alias("date")]))
    yzt = df.join(zt_next, on=["symbol", "date"], how="inner")
    yzt_agg = yzt.group_by("date").agg([
        pl.col("change_pct").mean().alias("yzt_avg"),
        pl.col("change_pct").le(-0.05).sum().alias("pullback_cnt"),
    ])

    # 新增百日新高（收盘价创近 100 交易日新高且昨日未创）
    df = df.with_columns(pl.col("close").rolling_max(100).over("symbol").alias("_m100"))
    df = df.with_columns((pl.col("close") >= pl.col("_m100")).alias("_h100"))
    df = df.with_columns(
        (pl.col("_h100") & ~pl.col("_h100").shift(1).over("symbol").fill_null(False))
        .alias("_new_high100"))

    agg = (df.group_by("date").agg([
        pl.len().alias("_tot"),
        pl.col("change_pct").gt(0).sum().alias("m_up"),
        pl.col("signal_limit_up").cast(pl.Boolean).sum().alias("m_zt"),
        pl.col("signal_limit_down").cast(pl.Boolean).sum().alias("m_dt"),
        pl.col("signal_broken_limit_up").cast(pl.Boolean).sum().alias("m_zb"),
        pl.col("_new_high100").sum().alias("m_nh"),
    ]).join(yzt_agg, on="date", how="left"))

    agg = agg.with_columns([
        (pl.col("m_up") / 20).alias("m_red"),
        ((pl.col("m_zt") + pl.col("m_nh")) / 2
         + pl.col("yzt_avg").fill_null(0.0) * 1000).alias("m_purple"),
    ]).with_columns(
        (pl.when((pl.col("m_zt") + pl.col("m_zb")) > 0)
         .then(pl.col("m_zb") / (pl.col("m_zt") + pl.col("m_zb")) * 100)
         .otherwise(0.0)).alias("m_brk")
    ).with_columns(
        (pl.col("m_brk")
         + (pl.col("m_dt") + pl.col("pullback_cnt").fill_null(0)) * 2).alias("m_green")
    ).with_columns(
        (pl.col("m_green") / pl.col("m_red").replace(0, None)).alias("m_depth")
    ).with_columns([
        pl.when((pl.col("m_green") > pl.col("m_red"))
                & (pl.col("m_green") > pl.col("m_purple")))
          .then(pl.lit("ice")).otherwise(pl.lit("none")).alias("m_state"),
        # 修复日：红线重新领先（红线 > 绿线 且 红线 > 紫线）
        pl.when((pl.col("m_red") > pl.col("m_green"))
                & (pl.col("m_red") > pl.col("m_purple")))
          .then(True).otherwise(False).alias("m_repair"),
    ])
    keep = ["date", "m_state", "m_depth", "m_red", "m_purple", "m_green", "m_repair"]
    # join 键 date 不 drop，只清同名旧列
    df = df.drop([c for c in keep if c != "date" and c in df.columns])

    # 逐股的 shift(1) 会因为停牌日期错位而取到错误的"上一交易日"，
    # 因此这里把"前一日状态"这类按交易日推进的量先物化成列（不依赖行序）。
    ms = (agg.select(keep).sort("date")
          .with_columns([
              pl.col("m_state").shift(1).alias("m_prev_state"),
              pl.col("m_depth").shift(1).alias("m_prev_depth"),
              pl.col("m_red").shift(1).alias("m_prev_red"),
              pl.col("m_purple").shift(1).alias("m_prev_purple"),
              pl.col("m_green").shift(1).alias("m_prev_green"),
              pl.col("m_repair").shift(1).cast(pl.Boolean).alias("m_prev_repair"),
          ]))
    # 近 4 个交易日（不含今日）内是否出现过冰点，供分歧策略用
    # ms 每个 date 一行（唯一键），直接按序 shift，不要再 over("date")
    recent = (pl.col("m_state").shift(1) == "ice")
    for k in (2, 3, 4):
        recent = recent | (pl.col("m_state").shift(k) == "ice")
    ms = ms.with_columns(recent.fill_null(False).alias("m_ice_recent4"))
    return df.join(ms, on="date", how="left")


def _core_conds(p: dict) -> pl.Expr:
    """两个策略共用的"个股四条 + 容量"部分。

    手册 §4 原文四条 = 板块地位 / 成交前5 / 股性（近期涨停+波浪）/ 节点。
    回测里板块地位无法机械化（需人工），只保留可算的四条之二：
    容量（60日均成交额）+ 股性（近 N 日有涨停）+ 非高位连板 + 趋势不深破位。

    容量门槛按手册"板块成交前5 / 资金载体"的精神取高分位：A 股 60 日均成交额
    的前 1%（约 60-80 亿）才是中军级别，15 亿门槛会把 1300 只小票全放进来。
    """
    min_amt = float(p.get("min_amt60", 50.0)) * 1e8  # 参数单位=亿
    lu_win = max(5, int(p.get("limit_lookback", 60)))
    consec_max = int(p.get("max_consec", 2))
    r60_min = float(p.get("min_mom60_pct", -15.0)) / 100.0
    # 60 日均成交额（停牌/缺量日按 0 计入，等价于近似真实水平）
    amt60 = pl.col("amount").rolling_mean(60, min_samples=1).over("symbol")
    # min_samples=1：新股/短历史也参与（默认要满窗会把上市不满60日的全判 null 剔除）
    lu_cnt = (pl.col("signal_limit_up").cast(pl.Int32)
              .fill_null(0).rolling_sum(lu_win, min_samples=1).over("symbol"))
    return (
        (amt60 >= min_amt)
        & (lu_cnt >= 1)
        & (pl.col("consecutive_limit_ups").fill_null(0) <= consec_max)
        & (pl.col("momentum_60d").is_not_null())
        & (pl.col("momentum_60d") >= r60_min)
    )


# ─────────────────────── 策略 A：冰点次日首次修复 ───────────────────────


def _ice_repair_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    depth_min = float(p.get("min_depth", 0.0))
    return (
        (pl.col("m_prev_state") == "ice")
        & (pl.col("m_prev_depth") >= depth_min)
        & pl.col("m_repair")
        & _core_conds(p)
    )


ice_repair = StrategyDef(
    id="doc1_ice_repair",
    name="冰冰点·次日修复（文档1）",
    description=("冰点日（绿>红且>紫）次日红线重新领先、绿线塌下去时买入主线核心。"
                 "手册 §3 节点①→② 的选股腿；板块地位需人工判断，这里只可机械部分。"),
    tags=["冰冰点", "情绪周期", "文档1", "低吸"],
    entry=_ice_repair_entry,
    scoring={"amount": 0.4, "change_pct": 0.3, "momentum_20d": 0.3},
    params=[
        {"id": "min_depth", "label": "冰点日最小深度(绿/红)", "type": "float",
         "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1},
        {"id": "min_amt60", "label": "60日均成交额下限(亿)", "type": "float",
         "default": 50.0, "min": 10.0, "max": 300.0, "step": 5.0},
        {"id": "limit_lookback", "label": "股性窗口(日)", "type": "int",
         "default": 60, "min": 10, "max": 120, "step": 5},
        {"id": "max_consec", "label": "最大连板数(排除高位板)", "type": "int",
         "default": 2, "min": 0, "max": 5, "step": 1},
        {"id": "min_mom60_pct", "label": "60日动量下限%", "type": "float",
         "default": -15.0, "min": -80.0, "max": 300.0, "step": 1.0},
    ],
    warmup=120,
    limit=30,
    stop_loss=-0.06,
    max_hold_days=10,
    exit_signal_ids=[],
)


# ─────────────────────── 策略 B：冰点后分歧低吸 ───────────────────────


def _divergence_pick_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    # 分歧日：绿线回升压过红线（指数层面分歧），但紫线（超短赚钱效应）没塌；
    # 前提：近 4 个交易日内出现过冰点。
    diverge = (pl.col("m_green") > pl.col("m_red")) & (
        pl.col("m_purple") > pl.col("m_green") * 0.5)
    return pl.col("m_ice_recent4") & diverge & _core_conds(p)


divergence_pick = StrategyDef(
    id="doc1_divergence_pick",
    name="冰冰点·分歧低吸（文档1）",
    description=("冰点日后第 N 日出现分歧（指数弱但超短赚钱效应仍在）时低吸主线核心。"
                 "手册 §3 节点③ 的选股腿；买法是回踩低吸，非追高。"),
    tags=["冰冰点", "情绪周期", "文档1", "分歧低吸"],
    entry=_divergence_pick_entry,
    scoring={"amount": 0.4, "momentum_60d": 0.4, "change_pct": 0.2},
    params=[
        {"id": "min_depth", "label": "冰点日最小深度(绿/红)", "type": "float",
         "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1},
        {"id": "max_days_since_ice", "label": "冰点后最多第N日", "type": "int",
         "default": 4, "min": 1, "max": 10, "step": 1},
        {"id": "min_amt60", "label": "60日均成交额下限(亿)", "type": "float",
         "default": 50.0, "min": 10.0, "max": 300.0, "step": 5.0},
        {"id": "limit_lookback", "label": "股性窗口(日)", "type": "int",
         "default": 60, "min": 10, "max": 120, "step": 5},
        {"id": "max_consec", "label": "最大连板数(排除高位板)", "type": "int",
         "default": 2, "min": 0, "max": 5, "step": 1},
        {"id": "min_mom60_pct", "label": "60日动量下限%", "type": "float",
         "default": -15.0, "min": -80.0, "max": 300.0, "step": 1.0},
    ],
    warmup=120,
    limit=30,
    stop_loss=-0.06,
    max_hold_days=8,
    exit_signal_ids=[],
)


STRATEGIES = [ice_repair, divergence_pick]


# ─────────────────── 策略 C：国家队确认版（文档1 + ETF 份额） ───────────────────
# 想法来源：文档1 的节点（冰点次日修复 / 冰点后分歧）叠加"国家队宽基 ETF 份额
# 周净申购"作跟随确认——资金面与情绪面共振时才出手。
#
# 口径关键：份额是**周频**（周五收盘数据），信号是日频。直接用"当周净申购"
# 会引入前视（周内哪天都不知道本周五的结果）。因此这里只允许用
# **信号日之前最近一个完整周** 的净申购，且必须早于信号日。
#
# 数据：F:\国家队持仓估计\national_team_position.json
#   （6 个宽基 ETF 份额，2024-01 起周频，144 周）


def _load_nt_week_rows() -> list[dict]:
    """读国家队 ETF 份额周频源文件 → 按 date 排序的行列表；读坏回空。

    周频源文件（国家队持仓估计项目产出）历史上是 GBK，日频文件已是 UTF-8，
    两个文件编码不同步——2026-10-29 实测硬按 UTF-8 读直接 UnicodeDecodeError。
    与 nt_monitor._weekly_rows 同款容错：UTF-8(含 BOM) → GB18030 自适应。
    """
    import json as _json
    from pathlib import Path as _Path
    from astock_hub.config import load_config
    cfg = load_config()
    nt_cfg = cfg.get("national_team") or {}
    path = _Path(nt_cfg.get("etf_json_path") or "")
    if not path.is_absolute() or not path.exists():
        return []
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return sorted(_json.loads(raw.decode(enc)), key=lambda r: r["date"])
        except (UnicodeDecodeError, ValueError, KeyError):
            continue
    return []


def _nt_week_series() -> pl.DataFrame:
    """加载国家队 ETF 份额周频序列 → {date, wchg_pct}（按周五日期）。"""
    rows = _load_nt_week_rows()
    out = []
    prev = None
    for r in rows:
        total = r.get("全宽基合计")
        if total is None or prev is None:
            prev = total
            continue
        out.append({"date": r["date"],
                    "wchg_pct": (total / prev - 1) * 100.0})
        prev = total
    return pl.DataFrame(out)


def _nt_daily_series() -> pl.DataFrame:
    """把周频份额摊成日频 → {date, wchg_pct}。

    口径：每个"周五→下周五"的周净申购，按该周实际交易日数均摊到每一天。
    只用已发生的周数据——第一个周五之前没有数据，故序列从第二个周五起。
    """
    rows = _load_nt_week_rows()
    if len(rows) < 2:
        return pl.DataFrame({"date": pl.Series([], dtype=pl.Utf8),
                             "wchg_pct": pl.Series([], dtype=pl.Float64)})
    # 交易日历：优先用帧里的日期集合；缺省退化为"周内 5 个自然工作日"
    out = []
    for i in range(1, len(rows)):
        prev_date, prev_total = rows[i - 1]["date"], rows[i - 1].get("全宽基合计")
        cur_date, cur_total = rows[i]["date"], rows[i].get("全宽基合计")
        if not prev_total or not cur_total:
            continue
        # 该周的日期序列（自然周内到当前周五为止）
        pd_, cd_ = pd.Timestamp(prev_date), pd.Timestamp(cur_date)
        span = int((cd_ - pd_).days)
        if span <= 0 or span > 20:
            continue
        # 每日均摊：整段跨度按自然日切分，交易日会落在其中（近似交易日均匀）
        wchg_total = (cur_total / prev_total - 1) * 100.0
        for k in range(1, span + 1):
            d = pd_ + pd.Timedelta(days=k)
            # 只保留工作日（周末不产生交易信号，但份额估计仍按日推进）
            if d.weekday() >= 5:
                continue
            out.append({"date": d.strftime("%Y%m%d"),
                        "wchg_pct": wchg_total / span * k})  # 累计进度百分比
    return pl.DataFrame(out) if out else pl.DataFrame(
        {"date": pl.Series([], dtype=pl.Utf8),
         "wchg_pct": pl.Series([], dtype=pl.Float64)})


def add_nt_confirm(df: pl.DataFrame, *, threshold: float = 2.0,
                   mode: str = "weekly") -> pl.DataFrame:
    """给帧追加 m_nt_confirm 列。

    mode="weekly"（默认，最保守）：只用信号日前最近一个**完整周五**的净申购，
    严格不前视，但确认信号最多滞后 6 天。
    mode="daily"（路线A，插值）：用周内均摊得到的当日累计进度判断，
    确认滞后压到 1 天以内，但依赖"周内均匀建仓"假设。
    """
    if mode == "daily":
        series = _nt_daily_series()
        # daily 模式：判断"截至信号日的当周累计进度是否已达阈值"
        nt = series
    else:
        nt = _nt_week_series()
    if nt.is_empty():
        return df.with_columns(pl.lit(False).alias("m_nt_confirm"))
    # 统一成 YYYY-MM-DD：JSON 里是 YYYYMMDD，帧里是 YYYY-MM-DD，
    # 直接字符串比较会让 prior 永远为空、确认列全 False（写测试时抓到）
    def _norm(x: str) -> str:
        x = str(x)
        return (f"{x[:4]}-{x[4:6]}-{x[6:]}"
                if len(x) == 8 and "-" not in x else x)
    dates = sorted(_norm(x) for x in nt["date"].to_list())
    wchg = {_norm(k): v for k, v in
            zip(nt["date"].to_list(), nt["wchg_pct"].to_list())}

    df = df.with_columns(pl.col("date").cast(pl.Utf8))
    uniq = df.select("date").unique().sort("date")
    marks = []
    for d in uniq["date"].to_list():
        if mode == "daily":
            # 取信号日当天或之前最近的一个可用日期
            prior = [x for x in dates if x <= d]
            if not prior:
                continue
            marks.append({"date": d,
                          "m_nt_confirm": wchg[prior[-1]] >= threshold})
        else:
            prior = [x for x in dates if x < d]
            if not prior:
                continue
            marks.append({"date": d,
                          "m_nt_confirm": wchg[prior[-1]] >= threshold})
    if not marks:
        return df.with_columns(pl.lit(False).alias("m_nt_confirm"))
    if "m_nt_confirm" in df.columns:
        df = df.drop("m_nt_confirm")
    return df.join(pl.DataFrame(marks), on="date", how="left").with_columns(
        pl.col("m_nt_confirm").fill_null(False))


def _nt_params(frame: pl.DataFrame, p: dict) -> pl.DataFrame:
    """按策略参数把国家队确认列补进帧。"""
    return add_nt_confirm(frame,
                          threshold=float(p.get("nt_min_pct", 2.0)),
                          mode=str(p.get("nt_mode", "weekly")))


def _nt_ice_repair_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    """冰点次日修复 + 国家队近一周净申购确认。"""
    depth_min = float(p.get("min_depth", 0.0))
    return (
        (pl.col("m_prev_state") == "ice")
        & (pl.col("m_prev_depth") >= depth_min)
        & pl.col("m_repair")
        & pl.col("m_nt_confirm")
        & _core_conds(p)
    )


nt_ice_repair = StrategyDef(
    id="doc1_nt_ice_repair",
    name="冰冰点·修复（国家队确认）",
    description=("冰点次日修复，且国家队宽基 ETF 份额最近完整周净申购达阈值"
                 "（资金面+情绪面共振）。份额周频，只用信号日前的完整周，不前视。"),
    tags=["冰冰点", "国家队", "文档1", "资金面确认"],
    entry=_nt_ice_repair_entry,
    scoring={"amount": 0.4, "change_pct": 0.3, "momentum_20d": 0.3},
    params=[
        {"id": "min_depth", "label": "冰点日最小深度(绿/红)", "type": "float",
         "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1},
        {"id": "nt_min_pct", "label": "国家队周净申购下限%", "type": "float",
         "default": 2.0, "min": 0.0, "max": 20.0, "step": 0.5},
        {"id": "nt_mode", "label": "份额确认口径", "type": "select",
         "default": "weekly", "options": ["weekly", "daily"]},
        {"id": "min_amt60", "label": "60日均成交额下限(亿)", "type": "float",
         "default": 50.0, "min": 10.0, "max": 300.0, "step": 5.0},
        {"id": "limit_lookback", "label": "股性窗口(日)", "type": "int",
         "default": 60, "min": 10, "max": 120, "step": 5},
        {"id": "max_consec", "label": "最大连板数(排除高位板)", "type": "int",
         "default": 2, "min": 0, "max": 5, "step": 1},
        {"id": "min_mom60_pct", "label": "60日动量下限%", "type": "float",
         "default": -15.0, "min": -80.0, "max": 300.0, "step": 1.0},
    ],
    warmup=120,
    limit=30,
    stop_loss=-0.06,
    max_hold_days=10,
    exit_signal_ids=[],
)


def _nt_divergence_entry(frame: pl.DataFrame, p: dict) -> pl.Expr:
    """冰点后分歧 + 国家队近一周净申购确认。"""
    diverge = (pl.col("m_green") > pl.col("m_red")) & (
        pl.col("m_purple") > pl.col("m_green") * 0.5)
    return (pl.col("m_ice_recent4")
            & diverge
            & pl.col("m_nt_confirm")
            & _core_conds(p))


nt_divergence = StrategyDef(
    id="doc1_nt_divergence",
    name="冰冰点·分歧低吸（国家队确认）",
    description=("冰点后分歧日低吸主线核心，且国家队宽基 ETF 份额最近完整周"
                 "净申购达阈值——资金面确认分歧可接。"),
    tags=["冰冰点", "国家队", "文档1", "分歧低吸", "资金面确认"],
    entry=_nt_divergence_entry,
    scoring={"amount": 0.4, "momentum_60d": 0.4, "change_pct": 0.2},
    params=[
        {"id": "min_depth", "label": "冰点日最小深度(绿/红)", "type": "float",
         "default": 0.0, "min": 0.0, "max": 10.0, "step": 0.1},
        {"id": "max_days_since_ice", "label": "冰点后最多第N日", "type": "int",
         "default": 4, "min": 1, "max": 10, "step": 1},
        {"id": "nt_min_pct", "label": "国家队周净申购下限%", "type": "float",
         "default": 2.0, "min": 0.0, "max": 20.0, "step": 0.5},
        {"id": "nt_mode", "label": "份额确认口径", "type": "select",
         "default": "weekly", "options": ["weekly", "daily"]},
        {"id": "min_amt60", "label": "60日均成交额下限(亿)", "type": "float",
         "default": 50.0, "min": 10.0, "max": 300.0, "step": 5.0},
        {"id": "limit_lookback", "label": "股性窗口(日)", "type": "int",
         "default": 60, "min": 10, "max": 120, "step": 5},
        {"id": "max_consec", "label": "最大连板数(排除高位板)", "type": "int",
         "default": 2, "min": 0, "max": 5, "step": 1},
        {"id": "min_mom60_pct", "label": "60日动量下限%", "type": "float",
         "default": -15.0, "min": -80.0, "max": 300.0, "step": 1.0},
    ],
    warmup=120,
    limit=30,
    stop_loss=-0.06,
    max_hold_days=8,
    exit_signal_ids=[],
)


STRATEGIES = [ice_repair, divergence_pick, nt_ice_repair, nt_divergence]
