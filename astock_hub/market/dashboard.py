"""实时看板聚合（照搬 tickflow build_market_overview 的口径，三源：hithink/iFinD/tencent）。

- post 盘后/历史：全市场 enriched 帧来自 service._enriched（hithink 本地库），
  指数走 iFinD（最新日）或 index_daily（历史日），最新日换手率由 tencent 补。
- realtime 盘中：最近交易日 D 的结构底表（均线/高低/连板）+ tencent 全市场实时帧合并；
  沪深实时、北交所沿用 D 日（tencent 不支持 BJ）。

打分/雷达公式逐字对标 tickflow market_overview_builder.py，但聚合用 polars 表达式。
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime

import polars as pl

from ..adapters.hithink import HithinkAdapter
from ..adapters.tencent import TencentAdapter
from ..store import concepts as concepts_store
from ..store import market_data as md
from .sessions import market_session, session_progress

# 四只核心指数（照搬 tickflow index_const；第4只是 000680 科创综指，非 000688 科创50）
CORE_INDEX: dict[str, str] = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000680.SH": "科创综指",
}

# 底表（重计算）缓存：{(end, raw_mtime, qfq_mtime): frame}，single-flight 加锁防击穿。
# 带行情文件 mtime：16:00 链更新 raw/qfq 后同 end 也必须重算（否则梯队等指标
# 继续用链前旧帧；2026-11-12 用户截图 5 板显示 6 板的缓存层根因）。
_FRAME_LOCK = threading.Lock()
_frame_cache: dict[tuple, pl.DataFrame] = {}

# tencent 实时帧：single-flight + 12s TTL（多客户端/轮询共享，92片不放大）
_RT_LOCK = threading.Lock()
_rt_executor = ThreadPoolExecutor(max_workers=2)
_rt_slot: dict = {"ts": 0.0, "future": None}
_RT_TTL = 12.0

# 开盘红展示覆盖缓存：正结果 30s / 失败 60s（L2 源串行限速，别被面板轮询打爆）
_KPH_SLOT: dict = {"ts": 0.0, "neg_ts": 0.0, "data": None}
_KPH_TTL = 30.0
_KPH_NEG_TTL = 60.0

# 法定节假日集合：日频缓存（节假日表极少变，kph 挂了陈旧兜底）
_HOLIDAY_SLOT: dict = {"stamp": None, "dates": frozenset()}


def _holiday_set() -> frozenset | None:
    """法定节假日集合（开盘红 GetHoliday，日频缓存；失败→陈旧集合兜底，
    都没有 → None，调用方按"不可判"处理（前端回退旧启发式）。"""
    today = datetime.now().date().isoformat()
    if _HOLIDAY_SLOT["stamp"] != today:
        try:
            from ..kph_client import holiday_list
            _HOLIDAY_SLOT.update(stamp=today, dates=frozenset(holiday_list()))
        except Exception:  # noqa: BLE001 — L2 源失败不挡看板
            if not _HOLIDAY_SLOT["dates"]:
                return None
    return _HOLIDAY_SLOT["dates"]


def _trading_day_today(now: datetime | None = None) -> bool | None:
    """今天是否交易日（2026-10-19 方案B）：工作日且不在法定节假日表。

    横幅原用"本地数据落后 ≥1 个工作日"猜休市——每个交易日 16:25 链落地前
    必落后 1 天，于是全白天误标"非交易日"（用户 10-16 周五误判反馈）。
    周末可确定性判 False；kph 不可用且无缓存时工作日返回 None（前端回退
    旧启发式，不变得更糟）。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hs = _holiday_set()
    if hs is None:
        return None
    return now.date().isoformat() not in hs


def _kph_display_override(cfg: dict, as_of: str, now: datetime | None = None) -> dict | None:
    """开盘红实时情绪 → 看板展示覆盖（2026-10-13 用户拍板：涨跌分布/涨跌停以开盘红为主）。

    只在**最新交易日盘面**生效：盘中跳过（realtime 帧已是当日实时）、历史日期视图跳过、
    交易日 9:30 后本地库还没落今日（tdx 未发布）也跳过——开盘红永远是最新交易日盘面，
    不能盖到旧日期头上。失败/字段缺失/限速冷却 → None，调用方回落本地口径。
    """
    now = now or datetime.now()
    _, is_trading = market_session(now)
    if is_trading:
        return None
    try:
        if as_of != md.db_max_date(cfg):
            return None
        # 交易日 9:30 后：开盘红已切今日盘面；本地 end 还是昨日时日期对不上，不覆盖
        if (now.weekday() < 5 and now.time() >= dtime(9, 30)
                and as_of != now.date().isoformat()):
            return None
    except Exception:  # noqa: BLE001
        return None

    now_ts = time.time()
    if _KPH_SLOT["data"] is not None and now_ts - _KPH_SLOT["ts"] < _KPH_TTL:
        return _KPH_SLOT["data"]
    if _KPH_SLOT["data"] is None and now_ts - _KPH_SLOT["neg_ts"] < _KPH_NEG_TTL:
        return None
    try:
        from ..kph_client import market_emotion
        e = market_emotion()
        if e.get("up") is None or e.get("down") is None:
            raise RuntimeError("开盘红市场情绪缺 up/down")
    except Exception:  # noqa: BLE001 — L2 参考源失败不阻塞看板
        _KPH_SLOT.update(ts=0.0, neg_ts=now_ts, data=None)
        return None
    _KPH_SLOT.update(ts=now_ts, neg_ts=0.0, data=e)
    return e


def _apply_kph_display(breadth: dict, limit: dict, kph: dict) -> tuple[dict, dict]:
    """把开盘红口径写进展示头条：breadth 家数 + limit 涨跌停（ZT/DT 含 ST，参照口径）。

    梯队/炸板/封板率等明细仍为本地；radar/emotion 用覆盖后的值计算（口径唯一）。
    """
    k_up, k_down = int(kph["up"]), int(kph["down"])
    k_flat = int(kph.get("flat") or 0)
    k_total = k_up + k_down + k_flat
    if k_total <= 0:
        return breadth, limit
    breadth = {**breadth,
               "total": k_total, "up": k_up, "down": k_down, "flat": k_flat,
               "up_pct": k_up / k_total * 100, "down_pct": k_down / k_total * 100}
    zt, dt = kph.get("limit_up_incl_st"), kph.get("limit_down_incl_st")
    if zt is not None:
        limit = {**limit, "limit_up": int(zt)}
    if dt is not None:
        limit = {**limit, "limit_down": int(dt)}
    return breadth, limit


# ---------------------------------------------------------------- 入口

def build_dashboard(cfg: dict, as_of: str | None = None, *,
                    realtime: bool | None = None,
                    skip_turnover: bool = False) -> dict:
    """看板主入口。

    realtime：None=auto（盘中且未指定历史日期→realtime）；True/False 强制。
    skip_turnover：跳过换手率加载（首次加载优化）。
    """
    end = md.db_max_date(cfg)
    session, is_trading = market_session()

    # auto：盘中且用户未指定日期 → 实时；其余（盘后/指定历史日）→ post
    if realtime is None:
        use_realtime = bool(is_trading and as_of is None)
    else:
        use_realtime = realtime
    # 历史日回看：realtime 合并的是"今日实时帧"，与指定历史日互斥——以日期
    # 为准压成收盘口径（前端固定传 realtime=true，不拦会把回看日静默变成今天；
    # 2026-10-27 选日回看失效根因）
    if as_of and as_of != end:
        use_realtime = False
    as_of = as_of or end

    if use_realtime:
        data = _build_realtime(cfg)
    else:
        data = _build_post(cfg, as_of, skip_turnover=skip_turnover)

    data.setdefault("meta", {}).update({
        "mode": "realtime" if use_realtime else "post",
        "market_session": session,
        "is_trading": is_trading,
        "trading_day": _trading_day_today(),
        "db_max_date": end,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "turnover_loaded": not skip_turnover,
    })
    return data


# ---------------------------------------------------------------- 底表

def _market_file_stamp(cfg: dict) -> tuple[int, int]:
    """raw/qfq 主库 mtime（ns）；缺失文件记 0。缓存键的一部分。"""
    from ..store.parquet_store import _store_dir
    store = _store_dir(cfg)
    stamps = []
    for rel in ("kline_daily_raw/_all.parquet",
                "kline_daily/_all.parquet"):
        try:
            stamps.append((store / rel).stat().st_mtime_ns)
        except OSError:
            stamps.append(0)
    return stamps[0], stamps[1]


def _base_frame(cfg: dict, end: str, *, warmup_days: int = 90) -> pl.DataFrame:
    """全市场 enriched 帧（截至 end，含 ma60 预热），缓存 + single-flight。"""
    key = (end, *_market_file_stamp(cfg))
    with _FRAME_LOCK:
        if key in _frame_cache:
            return _frame_cache[key]
        cal = [d for d in md.calendar_days(cfg) if d <= end]
        if not cal:
            raise RuntimeError("交易日历为空")
        start = cal[-warmup_days] if len(cal) >= warmup_days else cal[0]
        frame = service_enriched(cfg, start, end)
        _frame_cache[key] = frame
        return frame


def service_enriched(cfg: dict, start: str, end: str) -> pl.DataFrame:
    # 延迟 import 避免与 market 包循环
    from . import service
    return service._enriched(cfg, start, end)


def invalidate_cache() -> None:
    with _FRAME_LOCK:
        _frame_cache.clear()


# ---------------------------------------------------------------- 指数

def _indices(cfg: dict, as_of: str, is_latest: bool) -> list[dict]:
    """指数收盘自算（本地 index_daily，缺档时该函数内部走腾讯指数日K兜底）。

    2026-10-08 起不再调用 iFinD 实时快照（额度耗尽，用户决策暂时全面停用）。
    """
    # 取 as_of 前一日 ~ as_of 的指数收盘自算（最新日同样适用：本地/腾讯都有当日值）
    cal = md.calendar_days(cfg)
    before = [d for d in cal if d < as_of]
    start = before[-1] if before else as_of
    out = []
    for sym, name in CORE_INDEX.items():
        try:
            df = md.index_daily(cfg, start, as_of, sym)
        except Exception:  # noqa: BLE001 — 远端抖动（含 WinError 10055）不许拖垮整个看板
            df = pl.DataFrame()
        last_price = change_pct = change_amount = None
        if not df.is_empty():
            rows = df.sort("date").rows(named=True)  # [{date, open, close, ...}] 标量字典，避免 Series 真值歧义
            last_price = rows[-1].get("close")
            if len(rows) >= 2 and rows[-2].get("close") and last_price is not None:
                change_amount = last_price - rows[-2]["close"]
                change_pct = change_amount / rows[-2]["close"] * 100
        out.append({"symbol": sym, "name": name, "last_price": last_price,
                    "change_pct": change_pct, "change_amount": change_amount})
    return out


# ---------------------------------------------------------------- A 股过滤

def _is_a_share(sym: pl.Expr) -> pl.Expr:
    """判断是否为 A 股（沪深主板、创业板、科创板、北交所）。"""
    pure = sym.str.split(".").list.first()
    return (
        pure.str.starts_with("000") |
        pure.str.starts_with("001") |
        pure.str.starts_with("002") |
        pure.str.starts_with("003") |
        pure.str.starts_with("300") |
        pure.str.starts_with("301") |
        pure.str.starts_with("600") |
        pure.str.starts_with("601") |
        pure.str.starts_with("603") |
        pure.str.starts_with("605") |
        pure.str.starts_with("688") |
        pure.str.starts_with("689") |
        pure.str.starts_with("920")
    )


def _filter_a_shares(frame: pl.DataFrame, cfg: dict | None = None) -> pl.DataFrame:
    """过滤只保留 A 股真股票（前缀规则 + name_map 成员双重校验）。

    通达信盘后包混入指数/债券/基金（000300.SZ 等前缀与股票撞车），单靠前缀
    会把指数成交额算进全市场——2026-09-30 实测虚增至 9.8 万亿（真实 1.56 万亿）。
    """
    if "symbol" not in frame.columns:
        return frame
    out = frame.filter(_is_a_share(pl.col("symbol")))
    if cfg is not None:
        try:
            names = md.symbol_names(cfg)
            out = out.join(names.select("symbol"), on="symbol", how="inner")
        except Exception:  # noqa: BLE001 — 名单不可用则退回前缀过滤
            pass
    return out


def amount_history(cfg: dict, days: int = 60) -> list[dict]:
    """近 N 个交易日的全市场成交额日序列（与看板成交额 KPI 同口径）。

    读本地仓库 kline_daily_raw/_all.parquet（16:00 链每日落库），A 股白名单
    过滤后按日对 amount 求和；列名兼容 symbol/thscode 两种历史 schema，
    仓库不存在或为空时返回空列表（端点降级，不抛错）。
    返回：[{"date": "YYYY-MM-DD", "total": 成交额（元）, "count": 当日股票数}]，日期升序。
    """
    from ..store.parquet_store import _store_dir
    from ..symbols import polars_is_a_share_stock

    days = max(1, min(int(days), 250))
    path = _store_dir(cfg) / "kline_daily_raw" / "_all.parquet"
    if not path.exists():
        return []

    lf = pl.scan_parquet(str(path))
    cols = lf.collect_schema().names()
    code_col = "symbol" if "symbol" in cols else ("thscode" if "thscode" in cols else None)
    if code_col is None or "amount" not in cols or "date" not in cols:
        return []

    tail_dates = (
        lf.select(pl.col("date").unique()).collect()["date"].sort().to_list()[-days:]
    )
    if not tail_dates:
        return []

    lazy = (
        lf.filter(pl.col("date").is_in(tail_dates))
        .select(["date", code_col, "amount"])
        .filter(polars_is_a_share_stock(pl.col(code_col)))
    )
    # 双重校验（与 _filter_a_shares 同口径）：盘后包里 000300.SZ 等指数行前缀
    # 与股票撞车，单靠前缀会把指数成交额算进来（实测 2026-10-23 虚增至 14 万亿）。
    # 仅名单获取失败时退回前缀过滤；join 本身的错误不吞（防止静默失效）。
    try:
        names = md.symbol_names(cfg).select("symbol").lazy()
    except Exception:  # noqa: BLE001
        names = None
    if names is not None:
        lazy = lazy.join(names, left_on=code_col, right_on="symbol", how="inner")

    agg = (
        lazy.group_by("date")
        .agg(pl.col("amount").sum().alias("total"), pl.len().alias("count"))
        .sort("date")
        .collect()
    )
    return [
        {"date": str(r["date"])[:10],
         "total": float(r["total"]),
         "count": int(r["count"])}
        for r in agg.to_dicts()
    ]


# ---------------------------------------------------------------- 盘后

def _build_post(cfg: dict, as_of: str, skip_turnover: bool = False) -> dict:
    """盘后数据构建。

    skip_turnover: 跳过换手率加载（首次加载优化，换手率不影响核心指标）
    """
    end = md.db_max_date(cfg)
    is_latest = as_of == end
    frame = _base_frame(cfg, end)
    day = frame.filter(pl.col("date") == pl.lit(as_of).str.to_date("%Y-%m-%d"))
    if day.is_empty():
        raise RuntimeError(f"{as_of} 无落库行情（本地库范围截至 {end}）")

    # 过滤只保留 A 股
    day = _filter_a_shares(day, cfg)

    day = _attach_name(cfg, day)
    indices = _indices(cfg, as_of, is_latest)

    # 换手率加载（可跳过，不影响核心指标）
    if not skip_turnover:
        turnover_map = _turnover_map(cfg, day, as_of, is_latest)
        if turnover_map:
            tdf = pl.DataFrame(
                {"symbol": list(turnover_map), "turnover_rate": list(turnover_map.values())})
            day = day.join(tdf, on="symbol", how="left")
        else:
            day = day.with_columns(
                pl.lit(None).cast(pl.Float64).alias("turnover_rate"))
    else:
        day = day.with_columns(
            pl.lit(None).cast(pl.Float64).alias("turnover_rate"))

    return _assemble(day, indices, as_of=as_of, mode="post", cfg=cfg)


def _attach_name(cfg: dict, day: pl.DataFrame) -> pl.DataFrame:
    names = md.symbol_names(cfg)
    # 名称表为空或 Null 型（远端失败且无缓存）→ 降级跳过 join，面板不崩
    if names.height == 0 or names.schema.get("symbol") == pl.Null:
        return day
    return day.join(names, on="symbol", how="left")


def _turnover_map(cfg: dict, day: pl.DataFrame, as_of: str,
                  is_latest: bool) -> dict:
    """最新日且 tencent 数据日期 == as_of 时补换手率；否则空（口径不符/历史日）。

    优化：使用缓存，避免每次请求都拉取全市场换手率（~5万只股票，非常慢）。
    """
    if not is_latest:
        return {}

    # 检查缓存（5分钟内有效）
    cache_key = f"turnover_{as_of}"
    now = time.time()
    if hasattr(_turnover_map, '_cache'):
        if cache_key in _turnover_map._cache:
            cached_time, cached_data = _turnover_map._cache[cache_key]
            if now - cached_time < 300:  # 5分钟
                return cached_data
    else:
        _turnover_map._cache = {}

    symbols = day["symbol"].to_list()
    tx = TencentAdapter(cfg.get("tencent", {})).snapshot_chunked(symbols)
    if tx.status != "ok":
        return {}
    if tx.meta.as_of != as_of:
        return {}  # 实时日期≠as_of（盘中当日未落库），不跨日配换手率

    result = {it["symbol"]: it.get("turnover_ratio")
              for it in tx.data.get("item", [])}

    # 缓存结果
    _turnover_map._cache[cache_key] = (now, result)

    return result


# ---------------------------------------------------------------- 盘中

def _tencent_rt(cfg: dict, universe: list[str]):
    """tencent 全市场实时帧：single-flight + 12s TTL。返回 DimensionResult。"""
    adapter = TencentAdapter(cfg.get("tencent", {}))
    with _RT_LOCK:
        now = time.monotonic()
        slot = _rt_slot
        if slot["future"] is not None and now - slot["ts"] < _RT_TTL:
            future = slot["future"]
        else:
            future = _rt_executor.submit(adapter.snapshot_chunked, universe)
            slot["ts"] = now
            slot["future"] = future
    return future.result()  # 锁外等待，并发请求共享同一结果


def _align_rt_today(system_today: str, rt_time: str | None) -> tuple[str, bool]:
    """腾讯实时数据日期 vs 系统日期：rt 是上一交易日 → today 对齐到 rt 日期。

    夜间/凌晨/周末/节假日 tencent 回吐上一交易日收盘数据（rt_time 停在最后
    交易日 16:15）。此前一律盖"今天"戳并丢弃 d_day 历史行，导致：涨跌家数
    错位、连板在 d_day 基数上多 +1（10 板显示 11 板）——2026-10-13 用户
    两次报"看板数据不对"的根因。对齐后 same-session 语义成立：不加新板、
    d_day 行保留，展示的就是最后交易日真实盘面。
    """
    rt_date = (rt_time or "")[:10]
    if len(rt_date) == 10 and rt_date[4] == "-" and rt_date < system_today:
        return rt_date, True
    return system_today, False


def _hist_before_d(hist: pl.DataFrame, d_day: str, today: str) -> pl.DataFrame:
    """历史段裁剪：只裁掉会被今日实时行**替代**的那一天。

    - d_day == today：实时行就是 d_day 当日 → 裁掉 d_day（由 cn 行替代）；
    - d_day < today（新交易日盘中、当日链未落库）：d_day 是无人替代的真历史，
      必须保留——原实现无条件 `< d_day` 会把"昨天"整段从帧里抹掉
      （2026-10-28 晨检出：缺 10-27 → 昨日涨停基准/滚动指标全错位）。
    """
    dd = pl.lit(d_day).str.to_date("%Y-%m-%d")
    if d_day < today:
        return hist.filter(pl.col("date") <= dd)
    return hist.filter(pl.col("date") < dd)


def realtime_today_frame(cfg: dict) -> tuple[pl.DataFrame, dict]:
    """构造「D 历史 + 今日实时」全市场帧（共享：看板/策略/产业链）。

    沪深=D 结构列 + tencent 实时列；北交所=沿用 D 日（tencent 不支持 BJ）。
    返回 (多日帧, meta)：多日帧 = D 之前历史 + 今日行（date 升序），今日行的
    列名/口径与历史一致（change_pct 小数、turnover_rate 百分数）。

    优化：添加缓存（30秒内有效），避免重复计算。
    """
    # 检查缓存（30 秒）。_cache 可能是 None——首调先置 None 的旧写法在页面
    # 并发首请求时，后到的调用解包 None 直接炸（2026-10-15 看板
    # "cannot unpack non-iterable NoneType"），且炸点在重建之前、首调若中途
    # 失败会永远停留 None。改为：只有 (时间, 结果) tuple 才算命中，None/缺失
    # 一律当 miss 重建（成功路径末尾照旧写入 tuple，自愈）。
    now = time.time()
    _cached = getattr(realtime_today_frame, "_cache", None)
    if isinstance(_cached, tuple) and len(_cached) == 2:
        cached_time, cached_result = _cached
        if now - cached_time < 30:  # 30秒缓存
            return cached_result

    d_day = md.db_max_date(cfg)
    today = datetime.now().strftime("%Y-%m-%d")

    hist = _base_frame(cfg, d_day).with_columns(
        pl.col("volume").shift(1).rolling_mean(5).over("symbol").alias("_prev5"))
    drow = hist.filter(
        pl.col("date") == pl.lit(d_day).str.to_date("%Y-%m-%d"))
    # 先过滤 A 股再取 universe：本地全市场帧含基金/债券/指数等（5 万+ 代码），
    # 不过滤会让 tencent 拉取量膨胀近 10 倍且数千无效代码各白烧一次重试
    # （曾导致 realtime 看板单次 80 秒+）
    drow = _filter_a_shares(drow, cfg)
    drow = _attach_name(cfg, drow)
    universe = drow["symbol"].to_list()

    rt = _tencent_rt(cfg, universe)
    items = rt.data.get("item", [])
    bj = rt.data.get("unsupported_bj", [])
    failed = rt.data.get("failed_symbols", [])
    cn_total = rt.data.get("cn_total", 0)
    coverage = len(items) / cn_total if cn_total else 0.0

    progress = session_progress()
    extrapolate = progress is not None and progress >= 0.05
    # rt_time 给到分钟：腾讯 as_of 只有日期，从条目的 14 位时间戳取最大者
    rt_times = [it["time"] for it in items
                if it.get("time") and len(it["time"]) >= 14]
    if rt_times:
        _t = max(rt_times)
        rt_time = (f"{_t[:4]}-{_t[4:6]}-{_t[6:8]} "
                   f"{_t[8:10]}:{_t[10:12]}:{_t[12:14]}")
    else:
        rt_time = rt.meta.as_of
    # 夜间/节假日腾讯回吐上一交易日 → today 对齐到 rt 日期（防盖"今天"戳）
    today, rt_stale = _align_rt_today(today, rt_time)
    meta = {
        "d_day": d_day, "today": today, "coverage": round(coverage, 3),
        "bj": bj, "failed": failed, "extrapolate": extrapolate,
        "rt_time": rt_time, "rt_stale": rt_stale,
        "all_failed": not items,
    }
    if not items:
        result = (hist.sort(["symbol", "date"]), meta)
        realtime_today_frame._cache = (now, result)
        return result

    struct = drow.select([
        "symbol", "name", "ma5", "ma20", "ma60", "high_60d", "low_60d",
        "consecutive_limit_ups", "_prev5", "vol_ratio_5d",
        "signal_limit_up", "signal_limit_down", "signal_broken_limit_up",
        "signal_n_day_high", "signal_n_day_low"])
    rtsel = pl.DataFrame(items).select([
        "symbol", "price", "open", "volume", "high", "low", "amount",
        "change_pct", "turnover_ratio", "limit_up_price", "limit_down_price"])
    j = struct.join(rtsel, on="symbol", how="inner")

    up_price = pl.col("limit_up_price")
    # 连板数：仅当实时日期晚于本地 D 日（新交易日尚未落库）才 +1。
    # d_day==today（收盘后/同日实时）沿用 D 日结构值——此前 rt_stale=False
    # 就一律 +1，导致 5 板显示 6 板（2026-11-12 用户截图根因）。
    if today <= d_day:
        consec = pl.col("consecutive_limit_ups")
    else:
        consec = (pl.when(pl.col("price") >= up_price - 0.005)
                  .then(pl.col("consecutive_limit_ups") + 1).otherwise(0))
    cn = j.with_columns([
        pl.col("price").alias("close"),
        (pl.col("change_pct") / 100).alias("change_pct"),
        pl.col("turnover_ratio").alias("turnover_rate"),
        pl.lit(today).str.to_date("%Y-%m-%d").alias("date"),
        (pl.col("price") >= up_price - 0.005).alias("signal_limit_up"),
        (pl.col("price") <= pl.col("limit_down_price") + 0.005)
            .alias("signal_limit_down"),
        ((pl.col("high") >= up_price - 0.005)
         & (pl.col("price") < up_price - 0.005))
            .alias("signal_broken_limit_up"),
        (pl.col("price") >= pl.col("high_60d")).alias("signal_n_day_high"),
        (pl.col("price") <= pl.col("low_60d")).alias("signal_n_day_low"),
        consec.alias("consecutive_limit_ups"),
        # 盘中外推同样要求分母 >0（2026-10-20：停牌复牌/脏 _prev5 防护，
        # 与 pipeline 的量比分母防护同理），否则回落存储口径
        pl.when(pl.lit(bool(extrapolate)) & (pl.col("_prev5") > 0))
          .then((pl.col("volume") / progress) / pl.col("_prev5"))
          .otherwise(pl.col("vol_ratio_5d")).alias("vol_ratio_5d"),
    ]).drop(["price", "turnover_ratio", "limit_up_price",
             "limit_down_price", "_prev5"])

    bjdf = (drow.filter(pl.col("symbol").is_in(bj))
            .select([
                "symbol", "name", "ma5", "ma20", "ma60", "high_60d", "low_60d",
                "consecutive_limit_ups", "vol_ratio_5d",
                "signal_limit_up", "signal_limit_down", "signal_broken_limit_up",
                "signal_n_day_high", "signal_n_day_low",
                "open", "volume", "high", "low", "amount",
                "close", "change_pct"])
            .with_columns([
                pl.lit(None).cast(pl.Float64).alias("turnover_rate"),
                pl.lit(today).str.to_date("%Y-%m-%d").alias("date")]))

    before_d = _hist_before_d(hist, d_day, today)
    multi = pl.concat([before_d, cn, bjdf], how="diagonal_relaxed") \
              .sort(["symbol", "date"])
    result = (multi, meta)

    # 缓存结果
    realtime_today_frame._cache = (now, result)

    return result


def _build_realtime(cfg: dict) -> dict:
    """看板实时：共享帧 → 截今日行装配；全失败回退 post。"""
    multi, meta = realtime_today_frame(cfg)
    today = meta["today"]

    if meta["all_failed"]:
        out = _build_post(cfg, meta["d_day"])
        out.setdefault("meta", {}).update(
            {"realtime_failed": True, "fallback": "post"})
        return out

    day = multi.filter(
        pl.col("date") == pl.lit(today).str.to_date("%Y-%m-%d"))

    # 过滤只保留 A 股
    day = _filter_a_shares(day, cfg)

    indices = _indices(cfg, today, True)

    return _assemble(
        day, indices, as_of=today, mode="realtime", cfg=cfg,
        extra_meta={
            "trend_basis": "prev_day_ma",
            "realtime_degraded": meta["coverage"] < 0.95,
            "coverage": meta["coverage"],
            "failed_symbols": meta["failed"],
            "bj_stale": True,
            "bj_count": len(meta["bj"]),
            "rt_time": meta["rt_time"],
            "vol_extrapolated": meta["extrapolate"],
        })


# ---------------------------------------------------------------- 装配

_BOARD_ORDER = ["沪主板", "深主板", "创业板", "科创板", "北交所", "其他"]
_BANDS = [
    ("<−5%", None, -0.05), ("−5~−3%", -0.05, -0.03),
    ("−3~−1%", -0.03, -0.01), ("−1~0%", -0.01, 0),
    ("0~1%", 0, 0.01), ("1~3%", 0.01, 0.03),
    ("3~5%", 0.03, 0.05), (">5%", 0.05, None),
]


# ---------------------------------------------------------------- 封板修正（P0）

def _compute_seal_correction(day: pl.DataFrame, cfg: dict, as_of: str,
                             limit_up: int, broken: int, limit_down: int
                             ) -> dict:
    """基于本地 enriched 数据修正涨跌停计数（非ST）。

    假涨停(fake_up)：触及涨停价但未封住（从 limit_pool 排除收盘封板的）。
    假跌停(fake_down)：触及跌停价但未封住。
    sealed_ready：是否成功获取到 limit_pool 用于修正。

    优化：缓存 hithink limit_pool 结果（避免每次请求）。
    """
    sealed_ready = False
    fake_up = 0
    fake_down = 0

    try:
        # 使用本地 enriched 数据统计非ST涨跌停（更可靠）
        if "signal_limit_up" in day.columns and "name" in day.columns:
            # 统计非ST涨停
            non_st_limit_up = day.filter(
                (pl.col("signal_limit_up").fill_null(False)) &
                (~pl.col("name").str.contains("(?i)ST|\\*ST|退"))
            )
            corrected_up = non_st_limit_up.height

            # 统计非ST跌停
            non_st_limit_down = day.filter(
                (pl.col("signal_limit_down").fill_null(False)) &
                (~pl.col("name").str.contains("(?i)ST|\\*ST|退"))
            )
            corrected_down = non_st_limit_down.height

            sealed_ready = True

            # 尝试获取 hithink 涨停池计算假涨停（带缓存）
            try:
                # 检查缓存（5分钟内有效）
                cache_key = f"limit_pool_{as_of}"
                now = time.time()
                cache_hit = False

                if hasattr(_compute_seal_correction, '_cache'):
                    if cache_key in _compute_seal_correction._cache:
                        cached_time, cached_data = _compute_seal_correction._cache[cache_key]
                        if now - cached_time < 300:  # 5分钟
                            fake_up = cached_data
                            cache_hit = True
                else:
                    _compute_seal_correction._cache = {}

                if not cache_hit:  # 缓存未命中
                    hi = HithinkAdapter(cfg.get("hithink", {}))
                    pool_up = hi.limit_pool(kind="up", size=200)
                    if pool_up.status == "ok":
                        pool_syms = set()
                        for it in (pool_up.data or {}).get("item") or []:
                            s = it.get("symbol") or it.get("thscode") or ""
                            if s:
                                pool_syms.add(s.upper())
                        sealed_syms = set(
                            non_st_limit_up["symbol"].str.to_uppercase().to_list()
                        )
                        fake_up = max(0, len(pool_syms - sealed_syms))
                        # 缓存结果（无论是否有数据都缓存）
                        _compute_seal_correction._cache[cache_key] = (now, fake_up)
            except Exception:  # noqa: BLE001
                pass
        else:
            # 回退：使用原始信号（不区分ST）
            corrected_up = limit_up
            corrected_down = limit_down
    except Exception:  # noqa: BLE001
        pass  # 降级：使用 enriched 帧的信号计数

    return {
        "fake_up": fake_up, "fake_down": fake_down,
        "corrected_limit_up": corrected_up if 'corrected_up' in locals() else limit_up,
        "corrected_limit_down": corrected_down if 'corrected_down' in locals() else limit_down,
        "sealed_ready": sealed_ready,
    }


def _board_expr(sym: pl.Expr) -> pl.Expr:
    head = sym.str.slice(0, 3)
    return (
        pl.when(sym.str.ends_with(".BJ")).then(pl.lit("北交所"))
        .when(head.is_in(["300", "301"])).then(pl.lit("创业板"))
        .when(head.is_in(["688", "689"])).then(pl.lit("科创板"))
        .when(sym.str.ends_with(".SH")).then(pl.lit("沪主板"))
        .when(sym.str.ends_with(".SZ")).then(pl.lit("深主板"))
        .otherwise(pl.lit("其他")))


def _assemble(day: pl.DataFrame, indices: list[dict], *, as_of: str,
              mode: str, cfg: dict, extra_meta: dict | None = None) -> dict:
    cp = pl.col("change_pct")
    up_sig = pl.col("signal_limit_up").fill_null(False)
    down_sig = pl.col("signal_limit_down").fill_null(False)
    broken_sig = pl.col("signal_broken_limit_up").fill_null(False)
    nh = pl.col("signal_n_day_high").fill_null(False)
    nl = pl.col("signal_n_day_low").fill_null(False)

    m = day.select([
        pl.len().alias("total"),
        (cp > 0).sum().alias("up"),
        (cp < 0).sum().alias("down"),
        pl.col("amount").sum().alias("total_amount"),
        cp.mean().alias("avg_pct"),
        cp.median().alias("median_pct"),
        (cp >= 0.03).sum().alias("strong_up"),
        (cp <= -0.03).sum().alias("strong_down"),
        # 涨停/跌停计数：优先使用 hithink 涨跌停池数据（更准确）
        # 如果 hithink 数据不可用，回退到 signal_limit_up/down
        pl.len().alias("_placeholder"),  # 占位，后面会被 hithink 数据覆盖
        pl.len().alias("_placeholder2"),
        pl.len().alias("_placeholder3"),
        pl.col("consecutive_limit_ups").max().alias("max_boards"),
        (pl.col("close") >= pl.col("ma5")).sum().alias("above_ma5"),
        (pl.col("close") >= pl.col("ma20")).sum().alias("above_ma20"),
        (pl.col("close") >= pl.col("ma60")).sum().alias("above_ma60"),
        (nh | (pl.col("close") >= pl.col("high_60d"))).sum().alias("new_high"),
        (nl | (pl.col("close") <= pl.col("low_60d"))).sum().alias("new_low"),
        # is_finite 双保险（2026-10-20）：分母防护后 inf 不应再出现，均值仍
        # 只吃有限值——单只复牌/脏数据股不许拖垮全市场量比 KPI
        pl.col("vol_ratio_5d").filter(pl.col("vol_ratio_5d").is_finite())
          .mean().alias("avg_vol_ratio"),
        (pl.col("vol_ratio_5d") >= 1.5).sum().alias("high_vol_count"),
    ]).to_dicts()[0]

    total = m["total"] or 1
    flat = max(0, m["total"] - m["up"] - m["down"])

    # 使用 hithink 涨跌停池数据（更准确）
    from ..adapters.hithink import HithinkAdapter
    hi = HithinkAdapter(cfg.get("hithink", {}))

    # 获取涨停池
    pool_up = hi.limit_pool(kind="up", size=200)
    if pool_up.status == "ok":
        # 过滤非ST股票
        limit_up = len([it for it in (pool_up.data.get("item") or [])
                       if "ST" not in it.get("name", "") and "*ST" not in it.get("name", "")])
    else:
        limit_up = 0

    # 获取跌停池
    pool_down = hi.limit_pool(kind="down", size=200)
    if pool_down.status == "ok":
        # 过滤非ST股票
        limit_down = len([it for it in (pool_down.data.get("item") or [])
                         if "ST" not in it.get("name", "") and "*ST" not in it.get("name", "")])
    else:
        limit_down = 0

    broken = 0  # 炸板数（暂设为0，后续可从其他数据源获取）
    seal_rate = limit_up / (limit_up + broken) * 100 if (limit_up + broken) else 0.0

    breadth = {
        "total": m["total"], "up": m["up"], "down": m["down"], "flat": flat,
        "up_pct": m["up"] / total * 100, "down_pct": m["down"] / total * 100,
        "avg_pct": m["avg_pct"], "median_pct": m["median_pct"],
        "strong_up": m["strong_up"], "strong_down": m["strong_down"],
    }
    trend = {
        "above_ma5": m["above_ma5"], "above_ma20": m["above_ma20"],
        "above_ma60": m["above_ma60"],
        "above_ma5_pct": m["above_ma5"] / total * 100,
        "above_ma20_pct": m["above_ma20"] / total * 100,
        "above_ma60_pct": m["above_ma60"] / total * 100,
        "new_high": m["new_high"], "new_low": m["new_low"],
    }
    activity = {
        "avg_turnover": _avg_turnover(day),
        "high_turnover": _high_turnover(day),
        "high_vol_ratio": m["high_vol_count"] / total * 100,
        "vol_ratio": m["avg_vol_ratio"],
    }

    tiers, max_boards = _tiers(day)
    # P0 封板修正：基于 hithink limit_pool 校正假涨停/假跌停
    seal_corr = _compute_seal_correction(day, cfg, as_of, limit_up, broken, limit_down)
    corrected_up = seal_corr["corrected_limit_up"]
    corrected_down = seal_corr["corrected_limit_down"]
    corrected_seal = (corrected_up / (corrected_up + broken) * 100
                      if (corrected_up + broken) else 0.0)
    limit = {
        "limit_up": corrected_up, "broken": broken,
        "failed": seal_corr["fake_up"],
        "limit_down": corrected_down, "max_boards": max_boards,
        "seal_rate": corrected_seal, "tiers": tiers,
        "sealed_ready": seal_corr["sealed_ready"],
        "fake_up": seal_corr["fake_up"],
        "fake_down": seal_corr["fake_down"],
    }

    # 开盘红展示覆盖（2026-10-13 用户拍板）：最新交易日盘面的涨跌分布/涨跌停
    # 以开盘红接口为主、本地兜底；历史日期/盘中/本地库落后时不覆盖
    kph = _kph_display_override(cfg, as_of)
    breadth_source = "local"
    if kph:
        breadth, limit = _apply_kph_display(breadth, limit, kph)
        breadth_source = "kaipanhong"

    concept_rank = _dimension_rank(day, cfg, "concept")
    industry_rank = _dimension_rank(day, cfg, "industry")
    radar, emotion = _radar_emotion(
        indices, breadth, activity, limit, tiers, concept_rank, industry_rank)

    meta = {"as_of": as_of, "mode": mode,
            "breadth_source": breadth_source,
            "turnover_available": activity["avg_turnover"] is not None}
    if extra_meta:
        meta.update(extra_meta)

    return {
        "as_of": as_of,
        "indices": indices,
        "breadth": breadth,
        "amount": {"total": m["total_amount"],
                   "avg": m["total_amount"] / total},
        "boards": _boards(day),
        "limit": limit,
        "distribution": _distribution(day),
        "trend": trend,
        "activity": activity,
        "radar": radar,
        "emotion": emotion,
        "top_gainers": _top_rows(day, "change_pct", True),
        "top_losers": _top_rows(day, "change_pct", False),
        "turnover_leaders": _top_rows(day, "amount", True),
        "active_leaders": _top_rows(day, "turnover_rate", True, required=True),
        "concept_rank": concept_rank,
        "industry_rank": industry_rank,
        "meta": meta,
    }


# 装配期取 cfg：由 _assemble 参数传入。


def _avg_turnover(day: pl.DataFrame):
    if "turnover_rate" not in day.columns:
        return None
    v = day["turnover_rate"].drop_nulls()
    return v.mean() if v.len() else None


def _high_turnover(day: pl.DataFrame):
    if "turnover_rate" not in day.columns:
        return 0
    return (day["turnover_rate"] >= 5).sum()


def _boards(day: pl.DataFrame) -> list[dict]:
    df = day.with_columns(_board_expr(pl.col("symbol")).alias("board"))
    rows = df.group_by("board").agg([
        pl.len().alias("count"),
        (pl.col("change_pct") > 0).sum().alias("up"),
        (pl.col("change_pct") < 0).sum().alias("down"),
        pl.col("amount").sum().alias("amount"),
    ])
    out = []
    for r in rows.sort("amount", descending=True).to_dicts():
        r["up_pct"] = r["up"] / (r["count"] or 1) * 100
        out.append(r)
    return sorted(out, key=lambda r: _BOARD_ORDER.index(
        r["board"] if r["board"] in _BOARD_ORDER else "其他"))


def _tiers(day: pl.DataFrame) -> tuple[list[dict], int]:
    """连板梯队：每档取前5只（按成交额降序），含名称、代码、成交额。"""
    hit = day.filter(pl.col("consecutive_limit_ups") > 0)
    if hit.is_empty():
        return [], 0
    max_boards = int(day["consecutive_limit_ups"].max())
    out = []
    for n in range(max_boards, 0, -1):
        grp = hit.filter(pl.col("consecutive_limit_ups") == n)
        stocks = (grp.sort("amount", descending=True)
                    .head(5)
                    .select(["symbol", "name", "amount"]).to_dicts())
        out.append({"boards": n, "count": grp.height, "stocks": stocks})
    return out, max_boards


def _distribution(day: pl.DataFrame) -> list[dict]:
    cp = pl.col("change_pct")
    expr = pl.when(cp < -0.05).then(pl.lit("<-5%"))
    chain = expr
    for label, low, high in _BANDS[1:]:
        if high is None:
            chain = chain.when(cp >= low).then(pl.lit(label))
        else:
            chain = chain.when((cp >= low) & (cp < high)).then(pl.lit(label))
    banded = day.select(chain.otherwise(None).alias("band"))
    counts = dict(banded.group_by("band").len().iter_rows())
    total = day.height or 1
    return [{"label": label, "count": int(counts.get(label, 0)),
             "pct": counts.get(label, 0) / total * 100}
            for label, _, _ in _BANDS]


def _dimension_rank(day: pl.DataFrame, cfg: dict, kind: str) -> dict:
    """概念/行业聚合：join hithink membership parquet，按成员名 group。"""
    map_df, _ = concepts_store.concept_map_df(cfg, kind)
    j = day.with_columns(pl.col("symbol").str.to_uppercase().alias("_sym_up"))
    j = j.join(map_df, on="_sym_up", how="inner")
    col = pl.col(kind)
    rows = j.group_by(col).agg([
        pl.len().alias("count"),
        pl.col("change_pct").mean().alias("avg_pct"),
        (pl.col("change_pct") > 0).sum().alias("up_count"),
        (pl.col("change_pct") < 0).sum().alias("down_count"),
        pl.col("amount").sum().alias("amount"),
    ])

    # 每组领涨股
    lead = (j.sort("change_pct", descending=True)
             .group_by(col).first()
             .select([col.alias("g"),
                      pl.col("symbol").alias("l_sym"),
                      pl.col("name").alias("l_name"),
                      pl.col("change_pct").alias("l_pct")]))
    lead_map = {r["g"]: r for r in lead.iter_rows(named=True)}

    items = []
    for r in rows.iter_rows(named=True):
        g = r[kind]
        le = lead_map.get(g, {})
        items.append({
            "name": g, "count": r["count"], "avg_pct": r["avg_pct"],
            "up_count": r["up_count"], "down_count": r["down_count"],
            "amount": r["amount"], "source_field": "hithink.membership",
            "leader": {"symbol": le.get("l_sym"), "name": le.get("l_name"),
                       "change_pct": le.get("l_pct")},
        })
    items = [x for x in items if x["avg_pct"] is not None]
    return {
        "leading": sorted(items, key=lambda x: x["avg_pct"], reverse=True)[:5],
        "lagging": sorted(items, key=lambda x: x["avg_pct"])[:5],
    }


def _top_rows(day: pl.DataFrame, key: str, descending: bool, *,
              limit: int = 8, required: bool = False) -> list[dict]:
    if key not in day.columns:
        return []
    df = day.drop_nulls(subset=key)
    if required and df.is_empty():
        return []
    df = df.sort(key, descending=descending).head(limit)
    out = []
    for r in df.iter_rows(named=True):
        out.append({
            "symbol": r.get("symbol"), "name": r.get("name"),
            "close": r.get("close"), "change_pct": r.get("change_pct"),
            "amount": r.get("amount"),
            "turnover_rate": r.get("turnover_rate"),
            "board": _board_py(str(r.get("symbol") or "")),
        })
    return out


def _board_py(symbol: str) -> str:
    if symbol.endswith(".BJ"):
        return "北交所"
    if symbol.startswith(("300", "301")):
        return "创业板"
    if symbol.startswith(("688", "689")):
        return "科创板"
    if symbol.endswith(".SH"):
        return "沪主板"
    if symbol.endswith(".SZ"):
        return "深主板"
    return "其他"


# ---------------------------------------------------------------- 雷达/情绪

def _score(value: float, low: float, high: float) -> int:
    if value is None:
        return 50
    if isinstance(value, float) and not math.isfinite(value):
        # NaN/inf（停牌零成交股 0/0、除权边界等）→ 中性 50：
        # 单个坏值不许炸掉整块雷达（2026-10-12 实盘看板崩于此）
        return 50
    if high <= low:
        return 50
    return max(0, min(100, round((value - low) / (high - low) * 100)))


def _radar_emotion(indices, breadth, activity, limit, tiers,
                   concept_rank, industry_rank):
    idx_pcts = [x.get("change_pct") for x in indices
                if x.get("change_pct") is not None]
    avg_index_pct = sum(idx_pcts) / len(idx_pcts) if idx_pcts else 0.0

    total = breadth["total"] or 1
    strong_diff = (breadth["strong_up"] - breadth["strong_down"]) / total * 100
    high_vol_pct = activity["high_vol_ratio"]
    tier2_count = sum(t["count"] for t in tiers if t["boards"] >= 2)

    mainline_items = concept_rank["leading"][:3] + industry_rank["leading"][:3]
    if mainline_items:
        ml_avg = max(x.get("avg_pct") or 0 for x in mainline_items)
        ml_cover = max((x.get("count") or 0) / total * 100
                       for x in mainline_items)
        ml_score = round(
            _score(ml_avg, -0.005, 0.03) * 0.65
            + _score(ml_cover, 1, 12) * 0.35)
    else:
        ml_score = 50

    radar = [
        {"key": "index", "label": "指数",
         "value": _score(avg_index_pct, -2.5, 2.5)},
        {"key": "profit", "label": "赚钱",
         "value": round(
             _score(breadth["up_pct"], 20, 80) * 0.45
             + _score(breadth["avg_pct"], -0.02, 0.02) * 0.25
             + _score(breadth["median_pct"], -0.02, 0.02) * 0.20
             + _score(strong_diff, -8, 8) * 0.10)},
        {"key": "money", "label": "量能",
         "value": round(
             _score(activity["vol_ratio"], 0.6, 1.8) * 0.70
             + _score(high_vol_pct, 2, 12) * 0.30)},
        {"key": "speculation", "label": "投机",
         "value": round(
             _score(limit["limit_up"], 5, 90) * 0.25
             + _score(limit["seal_rate"], 30, 85) * 0.35
             + _score(limit["max_boards"], 1, 8) * 0.25
             + _score(tier2_count, 0, 30) * 0.15)},
        {"key": "resilience", "label": "抗跌",
         "value": 100 - round(
             _score(breadth["down_pct"], 20, 80) * 0.55
             + _score(breadth["strong_down"] / total * 100, 1, 12) * 0.45)},
        {"key": "mainline", "label": "主线", "value": ml_score},
    ]
    score = round(sum(x["value"] for x in radar) / len(radar))
    if score >= 70:
        label = "强势"
    elif score >= 55:
        label = "偏暖"
    elif score >= 45:
        label = "震荡"
    elif score >= 30:
        label = "偏冷"
    else:
        label = "冰点"
    return radar, {"score": score, "label": label}
