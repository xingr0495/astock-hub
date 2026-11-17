"""大盘判研：情绪周期（情绪分 + 阶段）——取数、算指标、聚合、落盘的编排层。

数据链路：hithink 本地库全市场日线（前复权算指标 + 原始价算涨跌停）→ 剔除 ST
→ `indicators.pipeline.compute_all` 出信号 → `market.regime` 按日聚合 + 规则分类
→ `market.phase` 按完整日序标 6 阶段 → 落盘缓存。

为什么剔除 ST：主板 ST 在 2026-07 前享 5% 涨跌幅、且是跨行业的"状态桶"，混进涨停
宽度/高度会系统性抬高投机维度（弱市炒 ST 尤其明显）。剔除发生在指标计算**之前**，
这样 ST 的涨跌停也不用按 5% 特殊算——反正结果里不会有它们。
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from ..indicators.pipeline import compute_all, filter_halt_days
from ..store import concepts as concepts_store
from ..store import market_data as md
from . import mainline as ml
from . import regime as rg

logger = logging.getLogger(__name__)


def concepts_map(cfg: dict, kind: str, *, force: bool = False):
    """概念/行业成分映射的薄封装（store.concepts）。"""
    return concepts_store.concept_map_df(cfg, kind, force=force)


def ml_mainline(limit_rows: pl.DataFrame, map_df: pl.DataFrame, kind: str,
                filter_cfg: dict | None = None) -> pl.DataFrame:
    """主线排名的薄封装（market.mainline）。"""
    return ml.mainline_rows(limit_rows, map_df, kind, filter_cfg=filter_cfg)


def _jsonable_dict(obj):
    """递归把结构里的 date 转 ISO 字符串（主线/RPS 结果要 JSON 化）。"""
    import datetime as _dt

    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable_dict(v) for v in obj]
    return obj


def store_dir(cfg: dict) -> Path:
    return Path(cfg.get("hithink_data", {}).get("store_dir") or "data")


def _enriched(cfg: dict, start: str, end: str) -> pl.DataFrame:
    """全市场 enriched 帧（指标 + 信号），已剔除 ST、已过滤停牌日、**只留 A 股股票**
    （口径注册表：全市场帧含基金/转债等品种，货币ETF 会按 10% 规则误判涨停——
    2026-10-02 对账修复，见 symbols.is_a_share_stock）。"""
    frame = md.market_frame(cfg, start, end, with_raw=True)
    st = md.st_symbols(cfg)
    if st and "symbol" in frame.columns:
        frame = frame.filter(~pl.col("symbol").str.to_uppercase().is_in(st))
    if "symbol" in frame.columns:
        from ..symbols import polars_is_a_share_stock
        frame = frame.filter(polars_is_a_share_stock(pl.col("symbol")))
    if frame.is_empty():
        return frame
    # 先过滤掉 open=0 和 high=0 且没有有效 raw_close 的行
    # （通达信盘后包可能包含未交易数据，但如果有 raw_close 说明有数据）
    if "open" in frame.columns and "high" in frame.columns:
        if "raw_close" in frame.columns:
            # 有 raw_close 数据的行保留（即使 open/high=0）
            frame = frame.filter(
                ~((pl.col("open") == 0) & (pl.col("high") == 0) & (pl.col("raw_close").is_null() | (pl.col("raw_close") == 0)))
            )
        else:
            frame = frame.filter(~((pl.col("open") == 0) & (pl.col("high") == 0)))
    frame = filter_halt_days(frame)
    # 这里不传 name：ST 已被剔除，剩下的都是非 ST，按板块规则算涨跌停即可
    return compute_all(frame)


def compute_regime(cfg: dict, *, days: int = 60, force: bool = False) -> pl.DataFrame:
    """算最近 `days` 个交易日的情绪周期时序，落盘（含 phase）并返回全量历史。"""
    warm_start, target_start, end = md.window_for_regime(cfg, days)
    frame = _enriched(cfg, warm_start, end)
    if frame.is_empty():
        raise RuntimeError(f"取不到 {warm_start}~{end} 的全市场行情（本地库可能未初始化）")

    idx = md.index_pct_map(cfg, warm_start, end)
    rows = rg.run_regime(frame, idx)
    if rows.is_empty():
        raise RuntimeError("情绪周期聚合结果为空（enriched 缺 change_pct 等必需列）")

    # 丢掉指标预热段，只保留目标区间
    rows = rows.filter(pl.col("date") >= pl.lit(target_start).str.to_date("%Y-%m-%d"))
    return rg.replace_range(store_dir(cfg), rows)


def _jsonable(rows: list[dict]) -> list[dict]:
    """把行里的 date 转成 ISO 字符串（webapi/MCP/CLI 三处都要 JSON 化）。"""
    for r in rows:
        d = r.get("date")
        if hasattr(d, "isoformat"):
            r["date"] = d.isoformat()
    return rows


def _limit_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """主线专用：只留当日连板 ≥1 的行（涨停梯队的输入）。"""
    cols = [c for c in ("date", "symbol", "consecutive_limit_ups", "amount")
            if c in frame.columns]
    return frame.select(cols).filter(pl.col("consecutive_limit_ups") >= 1)


def compute_mainline(cfg: dict, *, days: int = 60) -> dict:
    """重算最近 `days` 个交易日的每日主线（概念+行业），落盘并返回摘要。"""
    from . import mainline as ml
    from ..store.concepts import MEMBERSHIP_NOTE

    warm_start, target_start, end = md.window_for_regime(cfg, days)
    frame = _enriched(cfg, warm_start, end)
    if frame.is_empty():
        raise RuntimeError(f"取不到 {warm_start}~{end} 的全市场行情")

    limit_rows = _limit_rows(frame)
    result = {"note": MEMBERSHIP_NOTE, "end": end, "kinds": {}}
    store = store_dir(cfg)
    for kind in ("concept", "industry"):
        map_df, _ = concepts_map(cfg, kind)
        rows = ml_mainline(limit_rows, map_df, kind)
        rows = rows.filter(
            pl.col("date") >= pl.lit(target_start).str.to_date("%Y-%m-%d"))
        ml.upsert_mainline_history(store, rows)
        result["kinds"][kind] = {"days": rows["date"].n_unique(),
                                 "rows": rows.height}
    return result


def mainline_snapshot(cfg: dict, *, days: int = 30, refresh: bool = False) -> dict:
    """每日主线排行快照（默认读落盘，refresh 时重算）。"""
    from . import mainline as ml
    from ..store.concepts import MEMBERSHIP_NOTE

    store = store_dir(cfg)
    # 需要重算的情形：显式要求、没落过盘，或落盘主线比本地库最新行情日旧
    # （data sync 后不会自动重算主线，靠这里发现并补）。
    need_compute = refresh or not ml.mainline_path(store).exists()
    if not need_compute:
        old = ml.load_mainline_history(store)
        try:
            need_compute = (old.is_empty()
                           or str(old["date"].max()) < md.db_max_date(cfg))
        except Exception:  # noqa: BLE001 — 库不可读时沿用落盘历史
            need_compute = False
    if need_compute:
        compute_mainline(cfg, days=max(days, 60))

    out = {"note": MEMBERSHIP_NOTE, "kinds": {}}
    for kind in ("concept", "industry"):
        df = ml.load_mainline_history(store, kind).sort(["date", "rank"])
        if not df.is_empty():
            latest = str(df["date"].max())
            df = df.filter(pl.col("date") >= pl.lit(latest).str.to_date("%Y-%m-%d")
                           - pl.duration(days=days * 2 + 10))
        out["kinds"][kind] = df.to_dicts()
    return _jsonable_dict(out)


def rps_snapshot(cfg: dict, *, days: int = 12, refresh: bool = False) -> dict:
    """板块涨幅轮动矩阵快照（概念+行业，最新在前）。

    `refresh` 只控制概念映射是否重建（全市场行情每次现算，窗口小、开销低）。
    """
    from .rps import rps_rotation, slice_days
    from ..store.concepts import MEMBERSHIP_NOTE

    days = max(7, min(30, days))
    warm_start, _target, end = md.window_for_regime(cfg, days)
    frame = _enriched(cfg, warm_start, end)
    if frame.is_empty():
        raise RuntimeError(f"取不到 {warm_start}~{end} 的全市场行情")
    daily = frame.select([c for c in ("date", "symbol", "change_pct")
                         if c in frame.columns])

    out = {"note": MEMBERSHIP_NOTE, "kinds": {}}
    for kind in ("concept", "industry"):
        map_df, _ = concepts_map(cfg, kind, force=refresh)
        full = rps_rotation(daily, map_df, kind)
        out["kinds"][kind] = slice_days(full, days)
    return out


def screen_asof_frame(cfg: dict, *, warmup: int = 80) -> tuple[pl.DataFrame, str]:
    """构造选股用的「多日全市场帧」，返回 (多日帧, as_of日)。

    必须保留多日、不能先截成 as_of 单日：策略 entry 里可能有 `.shift(1)`（取昨日的
    均线/高点），单日帧上 shift 必为空、会漏掉所有用到昨日数据的策略；滚动因子
    （limit_up_count_60d 等）也需要完整历史。run_strategy 求值后只取 as_of 日。
    """
    cal = md.calendar_days(cfg)
    end = md.db_max_date(cfg)
    cal_le = [d for d in cal if d <= end]
    if len(cal_le) < warmup + 2:
        raise RuntimeError(f"交易日历覆盖不足，需要 ~{warmup} 个交易日")
    start = cal_le[-warmup]

    frame = _enriched(cfg, start, end)
    from .strategy_factors import add_strategy_factors
    frame = add_strategy_factors(frame)
    return frame, end


def screen_strategy(cfg: dict, strategy_id: str, *, overrides: dict | None = None,
                    limit: int | None = None) -> dict:
    """跑某个内置策略，返回 as_of 日的入选名单与打分。"""
    from .strategies import get_strategy, run_strategy

    strategy = get_strategy(strategy_id)
    frame, end = screen_asof_frame(cfg, warmup=max(strategy.warmup + 10, 80))
    # entry 在多日帧上求值（shift 有效），as_of=end 只取当日命中
    try:
        hits = run_strategy(frame, strategy, overrides=overrides, limit=limit,
                            as_of=pl.lit(end).str.to_date("%Y-%m-%d"))
    except Exception as exc:
        # 入场依赖帧里没有的列（典型：high_turnover 需要 turnover_rate/流通股本，
        # 本地 10 年库无股本、无法算历史换手率）。给出明确口径，不崩、不误报 0。
        return {
            "strategy_id": strategy_id, "name": strategy.name,
            "as_of": end, "total": 0, "rows": [],
            "unavailable": (
                f"该策略需要当日换手率（流通股本），本地历史库不含股本、无法回算；"
                f"缺失字段：{getattr(exc, 'missing', exc)}"),
        }
    # 本地库 v_symbol.name 为空，名称走远端 name 映射补上（供前端显示）
    hits = md.attach_names(cfg, hits)

    return {
        "strategy_id": strategy_id,
        "name": strategy.name,
        "as_of": end,
        "total": hits.height,
        "rows": _jsonable(hits.to_dicts()),
    }


def screen_strategy_realtime(cfg: dict, strategy_id: str, *,
                             limit: int | None = None) -> dict:
    """盘中：在「D 历史 + 今日实时」帧上跑策略，返回今日命中（20s 轮询用）。

    实时帧含 turnover_rate，高换手类策略在盘中可正常选出（本地历史库无换手率）。
    """
    from datetime import date as _date
    from . import dashboard
    from .strategies import get_strategy, run_strategy

    strategy = get_strategy(strategy_id)
    frame, meta = dashboard.realtime_today_frame(cfg)
    today = _date.fromisoformat(meta["today"])

    if meta["all_failed"]:
        # 实时帧全失败：退回 D 日落库结果并标注
        out = screen_strategy(cfg, strategy_id, limit=limit)
        out["realtime_failed"] = True
        return out

    try:
        hits = run_strategy(frame, strategy, limit=limit, as_of=today)
    except Exception as exc:  # noqa: BLE001
        return {
            "strategy_id": strategy_id, "name": strategy.name,
            "as_of": meta["today"], "total": 0, "rows": [],
            "unavailable": f"实时选股失败：{getattr(exc, 'missing', exc)}"}

    if "name" not in hits.columns:
        hits = md.attach_names(cfg, hits)

    rows = _jsonable(hits.to_dicts())
    return {
        "strategy_id": strategy_id,
        "name": strategy.name,
        "as_of": meta["today"],
        "total": hits.height,
        "rows": rows,
        "is_trading": True,
        "coverage": meta["coverage"],
        "bj_stale": True,
        "rt_time": meta["rt_time"],
    }


def list_available_strategies() -> list[dict]:
    """策略目录（给前端列表用）。"""
    from .strategies import list_strategies

    return [{"id": s.id, "name": s.name, "description": s.description,
             "tags": s.tags, "params": s.params, "limit": s.limit}
            for s in list_strategies()]


def snapshot(cfg: dict, *, days: int = 60, refresh: bool = False) -> dict:
    """情绪周期快照：最新一天 + 近 N 日时序 + 最近一次阶段切换。

    `refresh=True` 时重算目标区间（否则直接读落盘历史，避免每次请求都跑全市场）。
    """
    path = rg.history_path(store_dir(cfg))
    need_compute = refresh or not path.exists()
    if not need_compute:
        hist = rg.load_history(store_dir(cfg))
        if hist.is_empty() or "phase" not in hist.columns:
            need_compute = True
        else:
            # 历史比本地库最新行情日旧 → 需要补算
            try:
                need_compute = str(hist["date"].max()) < md.db_max_date(cfg)
            except Exception:  # noqa: BLE001 — 库不可用时退回用既有历史
                need_compute = False
    if need_compute:
        hist = compute_regime(cfg, days=days)
    else:
        hist = rg.load_history(store_dir(cfg))

    if hist.is_empty():
        return {"latest": None, "history": [], "transition": None,
                "coverage": {"rows": 0, "earliest_date": None, "latest_date": None}}

    hist = hist.sort("date")
    tail = hist.tail(days)
    rows = _jsonable(tail.to_dicts())
    latest = rows[-1]
    return {
        "latest": latest,
        "state_label": rg.STATE_LABELS.get(latest.get("state"), latest.get("state")),
        "history": rows,
        "transition": rg.latest_transition(store_dir(cfg)),
        "coverage": rg.coverage(store_dir(cfg)),
    }
