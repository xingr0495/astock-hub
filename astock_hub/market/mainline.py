"""市场主线识别 —— 基于涨停梯队的每日概念/行业排名（从 tickflow
`backend/app/services/market_mainline.py` 移植）。

判据：主升阶段的主线 = 同一概念内涨停家数多、最高连板高、梯队档位填得满
（2 板到最高板之间不断层）。每日按概念聚合涨停梯队 → 截面 rank 归一 → 加权成主线分。

移植边界（与 regime 同一套分工）：
- 打分/排名逻辑（权重、阈值、rank 归一）**逐字照搬**。
- tickflow 的 repo、parquet 扫描、用户偏好/ST 名单缓存不搬：取数与剔除归 store 与
  service，本模块只吃两个 DataFrame：`limit_rows`（date/symbol/consecutive_limit_ups/
  amount，已是连板 ≥1 的行、已剔除 ST）与 `map_df`（_sym_up → 成员，来自
  store.concepts）。

口径限制：成分是快照无历史版本，用今天的成分回看历史，早年归属漂移；见
MEMBERSHIP_NOTE，调用方要把它带给前端。
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

MAINLINE_DIR = "mainline_history"
_TOP_PER_DAY = 30          # 每日持久化的主线数（按分数截断）
_INDUSTRY_LEVEL = 2        # tickflow 行业层级（本实现名称扁平，不再生效）
_MIN_LIMIT_UP = 3          # 单概念当日最少涨停家数（低于此不参与排名）

# 主线分权重：概念内涨停家数 / 最高连板 / 梯队档位数 / 二板以上家数
_SCORE_WEIGHTS = {
    "limit_up_count": 0.35,
    "max_boards": 0.25,
    "rungs_filled": 0.25,
    "ge2_count": 0.15,
}

# 属性/状态桶不是真行业主线（对齐 TSP sector_rotation 排除口径）：
# 融资融券、沪深股通、昨日涨停、次新、ST、预盈预增、高送转、壳资源等一律不参与排名。
ATTRIBUTE_SECTOR_BLACKLIST = {
    "融资融券", "沪深股通", "昨日涨停", "次新股", "新股", "ST板块", "ST股",
    "预盈预增", "预亏预减", "扭亏为盈", "高送转", "壳资源", "摘帽", "破净股",
    "低价股", "微盘股", "大盘股", "中盘股", "小盘股", "绩优股", "亏损股",
    "减持股", "增持股", "回购股", "退市风险", "退市整理", "停牌股",
}

DEFAULT_FILTER = {"min_members": 4, "max_members": 600,
                  "blacklist": ATTRIBUTE_SECTOR_BLACKLIST}


def mainline_path(data_dir: Path) -> Path:
    return Path(data_dir) / MAINLINE_DIR / "part.parquet"


def mainline_rows(limit_rows: pl.DataFrame, map_df: pl.DataFrame, kind: str = "concept",
                  filter_cfg: dict | None = None) -> pl.DataFrame:
    """计算每日主线排行。

    `limit_rows`：date, symbol, consecutive_limit_ups, amount（调用方已过滤连板 ≥1
    并剔除 ST）；`map_df`：_sym_up, <kind>（成员名）。

    返回列：date, kind, member, limit_up_count, ge2_count, max_boards, boards_sum,
    rungs_filled, leader_symbol, score, rank。空数据返回空表。
    """
    if kind not in map_df.columns:
        raise ValueError(f"map_df 里没有成员列 {kind!r}：{map_df.columns}")
    if limit_rows.is_empty() or map_df.is_empty():
        return pl.DataFrame()

    cfg = dict(DEFAULT_FILTER)
    if filter_cfg:
        cfg.update(filter_cfg)
    cfg["blacklist"] = set(cfg.get("blacklist") or ())

    # 成员数过滤：太宽的指数（融资融券等数千成分）与用户黑名单不参与排名
    need_filter = (cfg["min_members"] > 1 or cfg["max_members"] < 5000
                   or cfg["blacklist"])
    if need_filter:
        counts = map_df.group_by(kind).len().rename({"len": "_members"})
        counts = counts.filter(
            pl.col("_members").ge(cfg["min_members"])
            & pl.col("_members").le(cfg["max_members"])
            & ~pl.col(kind).is_in(sorted(cfg["blacklist"]))
        )
        map_df = map_df.join(counts.select(kind), on=kind, how="semi")
        if map_df.is_empty():
            return pl.DataFrame()

    rows = limit_rows.with_columns(
        pl.col("symbol").str.to_uppercase().alias("_sym_up"))
    joined = rows.join(map_df, on="_sym_up", how="inner")
    if joined.is_empty():
        return pl.DataFrame()
    joined = joined.rename({kind: "member"})

    agg = (
        joined.group_by(["date", "member"])
        .agg(
            pl.len().alias("limit_up_count"),
            (pl.col("consecutive_limit_ups") >= 2).sum().alias("ge2_count"),
            pl.col("consecutive_limit_ups").max().alias("max_boards"),
            pl.col("consecutive_limit_ups").sum().alias("boards_sum"),
            # 梯队档位数：连板 ≥2 的不同板高数量（2 板到最高板之间不断层）
            pl.col("consecutive_limit_ups")
              .filter(pl.col("consecutive_limit_ups") >= 2)
              .n_unique().alias("rungs_filled"),
            pl.col("symbol")
              .sort_by(pl.col("consecutive_limit_ups"), pl.col("amount"),
                       descending=[True, True])
              .first().alias("leader_symbol"),
        )
    )

    # 涨停家数太少的概念不参与排名
    agg = agg.filter(pl.col("limit_up_count") >= _MIN_LIMIT_UP)
    if agg.is_empty():
        return pl.DataFrame()

    # 截面 rank 归一（0-1）：分母 max(n-1,1) 保证单概念日不除零
    norm_exprs = []
    for col in _SCORE_WEIGHTS:
        norm_exprs.append(
            ((pl.col(col).rank(method="average") - 1.0)
             / pl.max_horizontal(pl.len().over("date") - 1, 1))
            .over("date").alias(f"_{col}_r"))
    agg = agg.with_columns(norm_exprs)
    agg = agg.with_columns(
        (100.0 * sum(_SCORE_WEIGHTS[col] * pl.col(f"_{col}_r")
                     for col in _SCORE_WEIGHTS)).alias("score"))
    agg = agg.with_columns(
        pl.col("score").rank(method="ordinal", descending=True)
          .over("date").alias("rank"))

    return (
        agg.filter(pl.col("rank") <= _TOP_PER_DAY)
        .drop([f"_{col}_r" for col in _SCORE_WEIGHTS])
        .with_columns(pl.lit(kind).alias("kind"))
        .select([
            "date", "kind", "member", "limit_up_count", "ge2_count",
            "max_boards", "boards_sum", "rungs_filled", "leader_symbol",
            "score", "rank",
        ])
        .sort(["date", "rank"])
    )


def load_mainline_history(data_dir: str | Path, kind: str = "concept") -> pl.DataFrame:
    """读取主线时序（全部 kind），不存在返回空表。"""
    p = mainline_path(data_dir)
    if not p.exists():
        return pl.DataFrame()
    try:
        df = pl.read_parquet(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("主线历史读取失败：%s", e)
        return pl.DataFrame()
    if df.is_empty() or "kind" not in df.columns:
        return df
    return df.filter(pl.col("kind") == kind)


def _with_date_str(df: pl.DataFrame) -> pl.DataFrame:
    """date 列统一成 %Y-%m-%d 字符串（生产 parquet 是 Date/Datetime，测试可能写字符串）。"""
    if df.schema.get("date") in (pl.Date, pl.Datetime):
        return df.with_columns(pl.col("date").dt.strftime("%Y-%m-%d"))
    return df


def _classify_df(df: pl.DataFrame, recent_days: int = 5) -> list[dict]:
    """五分类（与看板 lib/mainline.ts classifyLatest 同口径）：近 recent_days
    日出榜 ≤1=单日脉冲；否则 rank≤2=主线、≤4=次主线、余=轮动。"""
    if df.is_empty() or "member" not in df.columns:
        return []
    df = _with_date_str(df)
    dates = sorted(df["date"].unique().to_list())
    if not dates:
        return []
    latest = dates[-1]
    recent = set(dates[-recent_days:])
    recent_df = df.filter(pl.col("date").is_in(list(recent)))
    presence_rows = (recent_df.group_by("member")
                     .agg(pl.col("date").n_unique().alias("p"))
                     .to_dicts())
    presence = {r["member"]: r["p"] for r in presence_rows}
    out: list[dict] = []
    for r in df.filter(pl.col("date") == latest).sort("rank").iter_rows(named=True):
        p = presence.get(r["member"], 0)
        if p <= 1:
            cls = "单日脉冲"
        elif r["rank"] <= 2:
            cls = "主线"
        elif r["rank"] <= 4:
            cls = "次主线"
        else:
            cls = "轮动"
        out.append({"member": r["member"], "rank": r["rank"],
                    "presence": p, "cls": cls})
    return out


def latest_mainline_classified(data_dir: str | Path, kind: str = "concept",
                               recent_days: int = 5) -> list[dict]:
    """最新交易日主线的五分类（存储口径）。返回按 rank 升序的
    [{member, rank, presence, cls}]；无历史返回 []。"""
    return _classify_df(load_mainline_history(data_dir, kind), recent_days)


def intraday_mainline_names(cfg: dict, kind: str = "concept",
                            recent_days: int = 5) -> set[str] | None:
    """盘中实时主线/次主线名单（11-09"当日掉出主线当日可卖"用）。

    存储主线历史 + 今日实时梯队行（realtime_today_frame）合并后五分类，
    与看板 `_merge_mainline_realtime` 同口径。返回 None = **不判**：
    非盘中（今日已落库）/非交易日/名单为空——不拿存储旧名单冒充当日，
    主线真空日也不甩卖（与 CLI `if ml_names:` 才评估一致）。
    """
    from datetime import date as _date
    from .dashboard import realtime_today_frame
    from . import service as svc
    from ..adapters import astock_data as _asd

    frame, meta = realtime_today_frame(cfg)
    today, d_day = meta["today"], meta["d_day"]
    if today == d_day:
        return None
    if not _asd._beijing_trading_day_today(_date.fromisoformat(today)):
        return None
    limit_rows = (frame.filter(
        (pl.col("date") == pl.lit(today).str.to_date("%Y-%m-%d"))
        & (pl.col("consecutive_limit_ups") >= 1))
        .select(["date", "symbol", "consecutive_limit_ups", "amount"]))
    map_df, _ = svc.concepts_map(cfg, kind)
    rows_today = mainline_rows(limit_rows, map_df, kind)
    hist = _with_date_str(load_mainline_history(svc.store_dir(cfg), kind))
    if not hist.is_empty():
        hist = hist.filter(pl.col("date") != today)  # 剔存储今日行防重
    if not rows_today.is_empty():
        today_df = pl.DataFrame(
            [{**r, "date": str(r.get("date"))[:10]}
             for r in rows_today.to_dicts()])
        hist = (pl.concat([hist, today_df], how="vertical_relaxed")
                if not hist.is_empty() else today_df)
    names = {r["member"] for r in _classify_df(hist, recent_days)
             if r["cls"] in ("主线", "次主线")}
    return names or None


def upsert_mainline_history(data_dir: str | Path, new_rows: pl.DataFrame) -> None:
    """按 (date, kind) 整日覆盖；schema 以 new_rows 为权威（旧数据补缺失列）。"""
    if new_rows.is_empty() or "date" not in new_rows.columns:
        return
    p = mainline_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = pl.read_parquet(p) if p.exists() else pl.DataFrame()
    if old.is_empty():
        combined = new_rows
    else:
        kept = old.join(
            new_rows.select(["date", "kind"]).unique(),
            on=["date", "kind"], how="anti")
        kept = kept.select([
            pl.col(c) if c in kept.columns else pl.lit(None).alias(c)
            for c in new_rows.columns])
        combined = pl.concat([kept, new_rows.select(new_rows.columns)],
                             how="vertical_relaxed")
    combined.sort(["date", "kind", "rank"]).write_parquet(p)
