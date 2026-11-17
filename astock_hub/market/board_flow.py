"""板块资金榜服务层：开盘红主源 + 东财备胎（2026-10-14 主备易位，规格 §4.3）。

- 今日：开盘红 RealRankingInfo 实时；近5/10日：逐日历史聚合（金额可加总）。
- 东财降级只保今日口径；多周期降级如实报 unavailable，不拿今日冒充。
- 15:30 后首个交易日请求触发当日落盘（data/board_fflow_kph/，同日幂等；假日不落）。
- 回退开关：config [data_source] board_fund_flow = "kph" | "eastmoney"。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

from .. import kph_client as kph

logger = logging.getLogger(__name__)

_TTL_TODAY = 180      # 今日视图缓存 3 分钟
_TTL_MULTI = 600      # 多周期视图缓存 10 分钟

_CACHE: dict[tuple, tuple[float, dict]] = {}


def _now() -> datetime:
    return datetime.now()


def _store_dir(cfg: dict) -> Path:
    return Path(cfg.get("hithink_data", {}).get("store_dir") or "data")


def _is_trading_day_today(cfg: dict) -> bool:
    from ..store import market_data as md
    today = date.today().isoformat()
    try:
        return today in set(md.calendar_days(cfg))
    except Exception:  # noqa: BLE001 — 日历不可用时不落盘、不聚合历史
        return False


def _last_trading_days(cfg: dict, n: int) -> list[str]:
    from ..store import market_data as md
    today = date.today().isoformat()
    days = [d for d in md.calendar_days(cfg) if str(d) <= today]
    return [str(d) for d in days[-n:]]


def _is_trading_day(cfg: dict, day: str) -> bool | None:
    """day 是否交易日；日历不可用返回 None（无法判定）。

    本函数被 chain_guard（失败告警守夜人判定）跨模块复用——勿删。
    （资金榜显式选日期已不再用它硬拦，改探上游，2026-10-24。）
    """
    from ..store import market_data as md
    try:
        return day in {str(d) for d in md.calendar_days(cfg)}
    except Exception:  # noqa: BLE001
        return None


def _to_item(row: dict, rank: int) -> dict:
    amount = row.get("amount")
    net = row.get("net_inflow")
    item = {
        "rank": rank,
        "code": row.get("plate_id", ""),
        "name": row.get("name", ""),
        "change_pct": row.get("change_pct"),
        "main_net": net,
        "main_pct": (round(net / amount * 100, 2)
                     if amount and net is not None else None),
        "volume_ratio": row.get("volume_ratio"),
        "strength": row.get("strength"),
        "market_cap": row.get("market_cap"),
    }
    # 量比只在单日视图存在（区间行无此列，键不出现而非 None）
    if row.get("volume_ratio") is None:
        item.pop("volume_ratio")
    # 人气只在单日视图附带（今日/历史天梯聚合）；区间行无此键
    if row.get("popular_count") is not None:
        item["popular_count"] = row.get("popular_count")
    # 净流入天数只在多周期（区间统计）附带
    if row.get("inflow_days") is not None:
        item["inflow_days"] = row.get("inflow_days")
    return item


def _fetch_single(cfg: dict, board_type: str, limit: int, *,
                  trade_date: str | None = None,
                  force_history: bool = False) -> dict:
    """单日榜单（今日实时 / 历史某日），附同日天梯人气。

    trade_date=None 走今日实时端点；指定历史日期走历史端点（开盘红/开盘啦
    同源服务，任意交易日可回看——东财无此能力，历史路径不设东财降级）。
    force_history 强制历史端点（含今日）——上游盘后把当日数据切到历史端点。
    """
    extra = {"force_history": True} if force_history else {}
    rows = kph.sector_ranking(board_type=board_type, trade_date=trade_date,
                              limit=limit, **extra)
    try:
        popular = kph.popular_by_plate(
            kph.limit_ladder(trade_date=trade_date, **extra))
    except Exception:  # noqa: BLE001 — 人气失败不拖垮榜单
        popular = {}
    # 不原地改写 rows（上游/mock 可能复用 dict 引用），复制后再挂人气：
    # 先按 plate_id 精确匹配（精选 801xxx 同码命中），未命中回落按板块名
    # （行业 881xxx 与天梯 801xxx 两套编码，名称是唯一桥）
    rows = [{**r,
             "popular_count": (popular.get(r.get("plate_id", ""), 0)
                               or popular.get(r.get("name", ""), 0))}
            for r in rows]
    items = [_to_item(r, i + 1) for i, r in enumerate(rows[:limit])]
    payload = {"item": items, "total": len(items), "source": "kph",
               "degraded": False,
               "period": "today" if trade_date is None else "single"}
    if trade_date:
        payload["as_of"] = trade_date
    return payload


def _fetch_multi(cfg: dict, board_type: str, period: str, limit: int) -> dict:
    """近 N 交易日区间统计（GetInterviewsByDateZS 原生区间口径，2026-10-16 升级：
    1 发替代原逐日聚合循环，且带来净流入天数/区间强度两个原生指标）。"""
    n = 5 if period == "5d" else 10
    days = _last_trading_days(cfg, n)
    if not days:
        raise RuntimeError("交易日历不可用，无法聚合多周期资金")
    raw = kph.interval_stats_by_plate(days[0], days[-1], size=500)
    rows = [kph.parse_interval_plate_row(r) for r in raw
            if isinstance(r, (list, tuple))]
    # 区间接口不分板块桶（返回全板块混合）——按代码前缀近似对齐桶口径：
    # 881xxx=同花顺行业体系（行业桶），其余（801/803 等）=精选概念池
    is_industry = board_type == "industry"
    rows = [r for r in rows
            if r["plate_id"].startswith("881") == is_industry]
    ranked = sorted(rows, key=lambda x: x.get("net_inflow") or 0.0,
                    reverse=True)[:limit]
    items = [_to_item(r, i + 1) for i, r in enumerate(ranked)]
    return {"item": items, "total": len(items), "source": "kph",
            "degraded": False, "period": period,
            "as_of": f"{days[0]}~{days[-1]}"}


def _fetch_em_today(cfg: dict, board_type: str, limit: int, *, degraded: bool) -> dict:
    # 东财适配器只认 industry/concept/region；前端"精选板块"(selected) 按外部精选概念池口径映射 concept
    board_type = "concept" if board_type == "selected" else board_type
    from ..adapters.astock_data import AStockDataAdapter
    dim = AStockDataAdapter(cfg.get("a_stock_data", {})).board_fund_flow(
        board_type=board_type if board_type in ("industry", "concept", "region") else "industry",
        period="today", limit=limit)
    if getattr(dim, "status", None) != "ok":
        return {"item": [], "total": 0, "status": "unavailable",
                "hint": (dim.data or {}).get("hint", "资金数据不可用"),
                "source": "eastmoney", "degraded": degraded, "period": "today"}
    raw = (dim.data or {}).get("item") or []
    items = [{
        "rank": it.get("rank", i + 1), "code": it.get("code", ""),
        "name": it.get("name", ""), "change_pct": it.get("change_pct"),
        "main_net": it.get("main_net"), "main_pct": it.get("main_pct"),
        "market_cap": None,
    } for i, it in enumerate(raw[:limit])]  # 量比/强度/人气为开盘红口径，东财兜底无此键
    return {"item": items, "total": len(items), "source": "eastmoney",
            "degraded": degraded, "period": "today"}


def _maybe_fallback_last_day(cfg: dict, board_type: str, limit: int,
                             empty: dict) -> dict:
    """盘前/非交易日/盘后切换：今日实时榜为空 → 自动找最近有数据的交易日。

    候选梯子（2026-10-14 实测升级：上游盘后把当日数据从实时端点切到历史端点，
    且本地日历可能滞后于上游）：今日历史口径（force_history）→ 逐日回退最多
    7 个自然日。首个有数据者胜出；as_of 如实标注（as_of=今日时前端仍显示"今日"，
    且属于今日收盘档可正常落盘）。全部为空/失败 → 保留今日空榜，不降级东财。
    """
    from datetime import timedelta

    today = date.today()
    for back in range(0, 8):
        day = (today - timedelta(days=back)).isoformat()
        try:
            payload = _fetch_single(cfg, board_type, limit, trade_date=day,
                                    force_history=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("board_flow fallback try %s failed: %s", day, exc)
            continue
        if payload.get("item"):
            payload["period"] = "today"   # 语义仍是"当前视图"，回落由 as_of 标注
            return payload
    return empty


def _archive(cfg: dict, board_type: str, payload: dict) -> None:
    """15:30 后首个请求落盘当日快照（同日重写幂等）；盘中/非交易日不落。"""
    if not _is_trading_day_today(cfg):   # 日期守卫（设计 §4.3.2）：假日不落盘
        return
    now = _now()
    if now.hour * 60 + now.minute < 15 * 60 + 30:
        return
    day = date.today().isoformat()
    out = _store_dir(cfg) / "board_fflow_kph" / f"{day}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"date": day, "board_type": board_type,
           "fetched_at": now.isoformat(timespec="seconds"),
           "item": payload.get("item", [])}
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)


def snapshot(cfg: dict, board_type: str = "industry", period: str = "today",
             limit: int = 20, trade_date: str | None = None) -> dict:
    """统一入口。返回 {item, total, source, degraded, period[, as_of]} 或
    {status: unavailable, hint}。

    trade_date：自由选日期（YYYY-MM-DD）。今天/不传=实时路径；过去交易日=
    单日历史视图（仅开盘红/开盘啦主源，无东财降级）；未来/非交易日拦截。
    """
    if board_type not in ("industry", "selected", "region", "concept"):
        raise ValueError("board_type 须为 industry/selected/region/concept")
    if period not in ("today", "5d", "10d"):
        raise ValueError("period 须为 today/5d/10d")

    today_str = date.today().isoformat()
    if trade_date is not None:
        if trade_date > today_str:
            return {"item": [], "total": 0, "status": "unavailable",
                    "hint": "不能选择未来日期", "source": "kph",
                    "degraded": False, "period": "single"}
        if trade_date == today_str:
            trade_date = None      # 今日等价于不传（仍走实时路径）
    historical = trade_date is not None

    primary = (cfg.get("data_source") or {}).get("board_fund_flow", "kph")
    key = (primary, board_type, period, limit, trade_date)
    ttl = _TTL_TODAY if period == "today" and not historical else _TTL_MULTI
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]

    def _unavail(hint: str, source: str) -> dict:
        return {"item": [], "total": 0, "status": "unavailable",
                "hint": hint, "source": source, "degraded": False,
                "period": "single" if historical else period}

    try:
        if primary == "eastmoney":
            if historical:
                # 东财只有当前快照，历史单日给不了——如实报不可用，不冒充
                payload = _unavail("当前数据源不支持历史日期查询", "eastmoney")
            elif period != "today":
                payload = _unavail("当前数据源不支持多周期，请切回今日",
                                   "eastmoney")
            else:
                payload = _fetch_em_today(cfg, board_type, limit, degraded=False)
        else:
            kph_type = "selected" if board_type == "concept" else board_type
            if historical:
                # 显式选日期直接探上游（本地日历可能滞后——2026-10-24 实测
                # 日历卡 10-22、把有数据的 10-23 误判成非交易日）；上游空了
                # 如实报无数据，重复选择由缓存兜住防连环请求
                payload = _fetch_single(cfg, kph_type, limit,
                                        trade_date=trade_date)
                if not payload.get("item"):
                    payload = _unavail(
                        "该日期暂无数据（非交易日/休市，或上游尚未归档）",
                        "kph")
            elif period == "today":
                payload = _fetch_single(cfg, kph_type, limit)
                if not payload.get("item"):
                    # 盘前/非交易日今日榜为空 → 自动回落最近交易日（as_of 标注）
                    payload = _maybe_fallback_last_day(cfg, kph_type, limit,
                                                       payload)
            else:
                payload = _fetch_multi(cfg, kph_type, period, limit)
                payload["period"] = period
        # 落盘仅限「今日收盘快照」（历史/多周期不归档；回落快照 as_of=今日
        # 即当日历史口径的全天数据，同样属于今日档）
        _today_str = date.today().isoformat()
        if (primary == "kph" and payload.get("status") != "unavailable"
                and payload.get("period") == "today"
                and payload.get("as_of", _today_str) == _today_str):
            # 落盘 I/O 失败只告警，不影响已成功的主源结果（不触发东财降级）
            try:
                _archive(cfg, board_type, payload)  # 只归档开盘红口径，东财降级数据不混入
            except Exception as exc:  # noqa: BLE001
                logger.warning("board_flow archive failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — 主源挂 → 东财降级（仅今日）
        logger.warning("board_flow primary failed: %s", exc)
        if historical:
            payload = _unavail(f"该日资金数据暂不可用：{exc}", "kph")
            payload["degraded"] = True
        elif period != "today":
            payload = {"item": [], "total": 0, "status": "unavailable",
                       "hint": f"多日资金数据暂不可用：{exc}",
                       "source": "kph", "degraded": True, "period": period}
        else:
            payload = _fetch_em_today(cfg, board_type, limit, degraded=True)

    _CACHE[key] = (time.monotonic(), payload)
    return payload
