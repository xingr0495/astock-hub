"""全市场行情数据的取数层（支持 a-stock-data 或 hithink 作为主数据源）。

支持的数据源配置：
- primary = "astock_data"：使用 a-stock-data（通达信盘后包 + 腾讯K线，免费）
- primary = "hithink"：使用 hithink-finance CLI（需凭证）

路由逻辑：market_frame() 根据配置选择主数据源，失败时回退到 fallback。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from .. import models
from ..adapters.hithink import HithinkAdapter

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_SHARES_PER_LOT = 100  # 1 手 = 100 股

_PANEL_COLS = "thscode, date, open, high, low, close, volume, amount"


def _cache_dir(cfg: dict) -> Path:
    base = Path(cfg.get("hithink_data", {}).get("cache_dir") or "data/hithink_cache")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _data_root(cfg: dict) -> Path:
    """数据根目录（不依赖进程 cwd）：优先由 cache_dir 推导，缺省锚定包根目录。"""
    cache = cfg.get("hithink_data", {}).get("cache_dir")
    if cache:
        return Path(cache).parent
    return Path(__file__).resolve().parents[2] / "data"


def _adapter(cfg: dict) -> HithinkAdapter:
    return HithinkAdapter(cfg.get("hithink_data", {}) or cfg.get("hithink", {}))


def _primary_source(cfg: dict) -> str:
    """获取主数据源配置（默认 astock_data）"""
    return cfg.get("data_source", {}).get("primary", "astock_data")


def _fallback_source(cfg: dict) -> str:
    """获取回退数据源配置（默认 hithink）"""
    return cfg.get("data_source", {}).get("fallback", "hithink")


def _to_date(df: pl.DataFrame) -> pl.DataFrame:
    """把 date 列统一成 pl.Date（导出可能是 'YYYY-MM-DD' 字符串或 Date）。"""
    if "date" not in df.columns:
        return df
    if df["date"].dtype == pl.Date:
        return df
    s = pl.col("date").cast(pl.Utf8).str.slice(0, 10)
    return df.with_columns(
        pl.when(s.str.contains("-"))
          .then(s.str.to_date("%Y-%m-%d", strict=False))
          .otherwise(s.str.replace_all("-", "").str.to_date("%Y%m%d", strict=False))
          .alias("date"))


def _normalize(df: pl.DataFrame) -> pl.DataFrame:
    """列名与单位对齐到内部口径。"""
    if "thscode" in df.columns:
        df = df.rename({"thscode": "symbol"})
    df = _to_date(df)
    if "volume" in df.columns:
        df = df.with_columns((pl.col("volume") / _SHARES_PER_LOT).alias("volume"))
    return df


def export_sql(cfg: dict, sql: str, cache_name: str, *, force: bool = False,
               timeout: int = 1800) -> pl.DataFrame:
    """执行只读 SQL 并把结果导出成 parquet（带本地缓存），返回 polars 帧。"""
    cache = _cache_dir(cfg) / f"{cache_name}.parquet"
    if force or not cache.exists():
        tmp = cache.with_suffix(".part.parquet")
        res = _adapter(cfg).db_export(sql, str(tmp), timeout=timeout)
        if res.status != models.OK:
            err = res.error or {}
            raise RuntimeError(
                f"本地库导出失败（{cache_name}）：{res.status} "
                f"{err.get('message', '')} {err.get('hint', '')}".strip())
        tmp.replace(cache)
    return pl.read_parquet(cache)


def _panel_sql(view: str, start: str, end: str) -> str:
    return (f"SELECT {_PANEL_COLS} FROM {view} "
            f"WHERE CAST(date AS DATE) BETWEEN DATE '{start}' AND DATE '{end}'")


def _export_panel(cfg: dict, start: str, end: str, *,
                  with_raw: bool = True, force: bool = False) -> pl.DataFrame:
    """用 data_panel 导出全市场面板（替代 db export，避免 DuckDB 内存限制）。"""
    cache_dir = _cache_dir(cfg)
    cache_dir.mkdir(parents=True, exist_ok=True)

    tag = f"panel_{start}_{end}"
    qfq_path = cache_dir / f"{tag}_qfq.parquet"
    raw_path = cache_dir / f"{tag}_raw.parquet"

    # 前复权面板
    if force or not qfq_path.exists():
        tmp = qfq_path.with_suffix(".part.parquet")
        ht = _adapter(cfg)
        res = ht.data_panel(start, end, str(tmp), timeout=900)
        if res.status != models.OK:
            raise RuntimeError(f"data_panel 导出失败：{res.status}")
        tmp.replace(qfq_path)

    qfq = _normalize(pl.read_parquet(qfq_path))

    if not with_raw:
        return qfq

    # 原始价面板（用于涨跌停信号）
    if force or not raw_path.exists():
        tmp = raw_path.with_suffix(".part.parquet")
        ht = _adapter(cfg)
        res = ht.data_panel(start, end, str(tmp), timeout=900)
        if res.status != models.OK:
            raise RuntimeError(f"data_panel raw 导出失败：{res.status}")
        tmp.replace(raw_path)

    raw = _normalize(pl.read_parquet(raw_path))
    raw = raw.select([c for c in ("symbol", "date", "open", "high", "low", "close")
                      if c in raw.columns]).rename({
        "open": "raw_open", "high": "raw_high",
        "low": "raw_low", "close": "raw_close"})
    return qfq.join(raw, on=["symbol", "date"], how="left")


def market_frame(cfg: dict, start: str, end: str, *, with_raw: bool = True,
                 force: bool = False) -> pl.DataFrame:
    """全市场日线（前复权）+ 可选原始价。

    根据配置选择主数据源：
    - astock_data：a-stock-data 工具包（通达信盘后包，免费）
    - hithink：hithink-finance CLI（需凭证）

    首先尝试从日期分区仓库读取，失败时从主数据源获取并缓存。
    """
    from .parquet_store import _store_dir, read_market

    store = _store_dir(cfg)
    try:
        # 尝试从日期分区仓库读取
        return read_market(store, start, end, with_raw=with_raw)
    except RuntimeError:
        # 仓库不存在或数据不足，从主数据源获取
        primary = _primary_source(cfg)
        try:
            # 2026-10-13：axdata 升主后此键两消费方共用，全市场冷路径保持
            # tdx 盘后包（规格"落库不动"）；axdata 视为与 astock_data 同族。
            if primary in ("astock_data", "axdata"):
                return _tdx_market_frame(cfg, start, end, with_raw=with_raw)
            else:
                return _export_panel(cfg, start, end, with_raw=with_raw, force=force)
        except Exception as e:
            # 主数据源失败，尝试回退
            fallback = _fallback_source(cfg)
            logger.warning("主数据源 %s 失败: %s，尝试回退 %s", primary, e, fallback)
            if fallback == "hithink":
                return _export_panel(cfg, start, end, with_raw=with_raw, force=force)
            raise


def _tdx_market_frame(cfg: dict, start: str, end: str, *,
                      with_raw: bool = True) -> pl.DataFrame:
    """使用 a-stock-data 的通达信盘后包获取全市场日线。"""
    from .parquet_store import _store_dir

    store = _store_dir(cfg)
    raw_file = store / "kline_daily_raw" / "_all.parquet"

    if raw_file.exists() and not force:
        # 已有数据，检查是否需要更新
        df = pl.read_parquet(raw_file)
        last_date = str(df["date"].max())[:10]
        if last_date >= end:
            # 数据已覆盖请求范围，直接返回
            return _read_with_raw(store, start, end) if with_raw else \
                   df.filter((pl.col("date") >= start) & (pl.col("date") <= end))

    # 从通达信盘后包获取数据
    from ..adapters.astock_data import tdx_daily_package
    from datetime import datetime as dt

    # 获取日期范围内的所有交易日
    # 简单方案：逐日尝试（通达信非交易日会抛 ValueError）
    start_date = dt.strptime(start, "%Y-%m-%d").date()
    end_date = dt.strptime(end, "%Y-%m-%d").date()

    all_rows = []
    current = start_date
    net_failures = 0
    while current <= end_date:
        try:
            df = tdx_daily_package(current.isoformat())
            all_rows.extend(df.to_dicts())
            net_failures = 0
        except ValueError:
            pass  # 非交易日，跳过
        except Exception as e:
            # 网络类失败（超时/断连/非 zip）：连续 3 天失败说明远端不可达，
            # 立即放弃而不是逐日磨完整个预热窗口（每个超时 20s × 90 天 ≈ 30 分钟）
            net_failures += 1
            logger.warning("通达信盘后包 %s 失败: %s", current, e)
            if net_failures >= 3:
                raise RuntimeError(f"通达信盘后包连续 {net_failures} 天获取失败（远端不可达）：{e}")
        current += __import__("datetime").timedelta(days=1)

    if not all_rows:
        raise RuntimeError("无法从通达信盘后包获取数据")

    # 合并并写入缓存
    new_df = pl.DataFrame(all_rows)
    new_df = _normalize(new_df)

    if raw_file.exists():
        existing = pl.read_parquet(raw_file)
        new_df = pl.concat([existing, new_df]).unique(subset=["symbol", "date"], keep="last")

    store.mkdir(parents=True, exist_ok=True)
    new_df.write_parquet(raw_file)

    return _read_with_raw(store, start, end) if with_raw else \
           new_df.filter((pl.col("date") >= start) & (pl.col("date") <= end))


def _read_with_raw(store: Path, start: str, end: str) -> pl.DataFrame:
    """读取前复权 + 原始价数据"""
    from datetime import date as _date

    start_date = _date.fromisoformat(start) if isinstance(start, str) else start
    end_date = _date.fromisoformat(end) if isinstance(end, str) else end

    raw_file = store / "kline_daily_raw" / "_all.parquet"
    if not raw_file.exists():
        return pl.DataFrame()

    df = pl.read_parquet(raw_file)
    df = df.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))

    if "thscode" in df.columns:
        df = df.rename({"thscode": "symbol"})

    return df


def _index_daily_tencent(start: str, end: str, thscode: str) -> pl.DataFrame:
    """腾讯指数日K 兜底（hithink index history 返回 stale/失败时用，免费不封 IP）。"""
    import time as _time
    import urllib.error
    import urllib.request as _urlreq

    code, _, mkt = thscode.partition(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(mkt.upper(), "sh")
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={prefix}{code},day,,,60,qfq")
    raw: bytes | None = None
    last_err: Exception | None = None
    for attempt in range(2):  # 瞬态网络故障（WinError 10055 等）重试一次
        try:
            req = _urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlreq.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            break
        except (OSError, urllib.error.URLError) as e:
            last_err = e
            _time.sleep(2.0)
    if raw is None:
        raise RuntimeError(f"腾讯指数日K获取失败：{last_err}")
    import json as _json
    d = _json.loads(raw)
    kl = (d.get("data") or {}).get(f"{prefix}{code}") or {}
    raw = kl.get("qfqday") or kl.get("day") or []
    # 腾讯日K 列序：date, open, close, high, low, volume
    rows = []
    for r in raw:
        ds = str(r[0])[:10]
        if start <= ds <= end and len(r) >= 5:
            rows.append({"date": date.fromisoformat(ds),
                         "open": float(r[1]), "close": float(r[2]),
                         "high": float(r[3]), "low": float(r[4]),
                         "amount": None})
    return pl.DataFrame(rows).sort("date") if rows else pl.DataFrame()


def index_daily(cfg: dict, start: str, end: str,
                thscode: str = "000001.SH") -> pl.DataFrame:
    """指数日线（date/close 等）。本地库不含指数，走远端的 index history；
    远端 stale/失败时回退腾讯指数日K。"""
    try:
        res = _adapter(cfg).index_history(thscode, date.fromisoformat(start),
                                          date.fromisoformat(end))
        if res.status != models.OK:
            raise RuntimeError(f"指数 {thscode} 日线获取失败：{res.status}")
        rows = []
        for it in (res.data or {}).get("item") or []:
            ms = it.get("date_ms")
            if ms is None:
                continue
            rows.append({
                "date": datetime.fromtimestamp(int(ms) / 1000, tz=_CST).date(),
                "open": it.get("open_price"), "high": it.get("high_price"),
                "low": it.get("low_price"), "close": it.get("close_price"),
                "amount": it.get("turnover"),
            })
        if rows:
            return pl.DataFrame(rows).sort("date")
    except Exception:
        pass
    return _index_daily_tencent(start, end, thscode)


def index_pct_map(cfg: dict, start: str, end: str,
                  thscode: str = "000001.SH") -> dict:
    """{date: 指数涨跌幅(小数)} —— 情绪周期的「趋势」维度需要它。"""
    df = index_daily(cfg, start, end, thscode)
    if df.is_empty():
        return {}
    df = df.with_columns(
        (pl.col("close") / pl.col("close").shift(1) - 1).alias("pct"))
    return {r["date"]: float(r["pct"]) for r in df.iter_rows(named=True)
            if r.get("pct") is not None}


_CAL_TTL_DAYS = 7  # 日历缓存保质期：超期强制重取（2026-10-15 修复：原缓存
                    # 永不过期——远端 09-21 之后一直读旧档，对账/链的日期窗口全被拖偏）


def _calendar_from_kph() -> pl.DataFrame:
    """开盘红 GetHoliday 推导交易日：工作日 − 法定节假日（覆盖 today-400 ~ 年末）。

    hithink 日历查询失败时的回退源（2026-10-15 A 级接入）；节假日表 2015→今年，
    10-14 实测 316 条、2026-10 国庆档正确。
    """
    from ..kph_client import holiday_list
    holidays = set(holiday_list())
    today = datetime.now().date()
    d = today - timedelta(days=400)
    end = date(today.year, 12, 31)
    days: list[str] = []
    while d <= end:
        if d.weekday() < 5 and d.isoformat() not in holidays:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return pl.DataFrame({"date": days})


def calendar_days(cfg: dict, *, force: bool = False) -> list[str]:
    """交易日历（YYYY-MM-DD 升序）。远端单次约一年，落盘缓存，**超过
    _CAL_TTL_DAYS 天强制重取**；主源 hithink 查询通道，失败回退开盘红节假日
    推导，双源都失败但有旧档 → 警告后回退旧档（不炸链）。"""
    out = _cache_dir(cfg) / "calendar.parquet"

    def _fresh() -> bool:
        if not out.exists():
            return False
        age = (datetime.now().date()
               - datetime.fromtimestamp(out.stat().st_mtime).date())
        return age.days < _CAL_TTL_DAYS

    if not force and _fresh():
        return pl.read_parquet(out)["date"].to_list()

    try:
        res = _adapter(cfg).calendar()
        if res.status != models.OK:
            raise RuntimeError(f"远端返回 {res.status}")
        items = (res.data or {}).get("item") or []
        if not items:
            raise RuntimeError("远端日历为空")
        df = pl.DataFrame({"date": [i.get("date") for i in items if i.get("date")]})
        df = df.with_columns(
            pl.col("date").str.to_date("%Y%m%d", strict=False)
              .dt.strftime("%Y-%m-%d").alias("date")).sort("date")
    except Exception as e:  # noqa: BLE001 — 主源失败走开盘红推导
        logger.warning("hithink 日历重取失败，回退开盘红节假日推导：%s", e)
        try:
            df = _calendar_from_kph()
        except Exception as e2:  # noqa: BLE001
            if out.exists():
                logger.warning("日历双源失败，回退已过期旧缓存：%s / %s", e, e2)
                return pl.read_parquet(out)["date"].to_list()
            raise RuntimeError(
                f"交易日历获取失败：{e}；开盘红回退也失败：{e2}") from e2
    df.write_parquet(out)
    return df["date"].to_list()


def st_symbols(cfg: dict, *, force: bool = False) -> list[str]:
    """风险警示股名单（名称含 ST，大写带交易所后缀）。

    情绪周期与主线**默认剔除 ST**：主板 ST 在 2026-07 前享 5% 涨跌幅、且是跨行业的
    "状态桶"，混进涨停宽度/高度会系统性抬高投机维度（弱市炒 ST 尤其明显）。
    本地库的 v_symbol.name 为空，所以名称只能走远端（按天缓存，摘帽/更名会变）。
    """
    out = _cache_dir(cfg) / "st_symbols.parquet"
    if not force and out.exists():
        age = datetime.now(_CST).date() - datetime.fromtimestamp(
            out.stat().st_mtime, tz=_CST).date()
        if age.days < 1:
            return pl.read_parquet(out)["symbol"].to_list()

    ht = _adapter(cfg)
    found: list[str] = []
    offset = 0
    while True:
        res = ht.symbol_list(offset=offset, limit=1000)
        if res.status != models.OK:
            logger.warning("ST 名单获取中断：%s", res.status)
            break
        items = (res.data or {}).get("item") or []
        if not items:
            break
        found += [str(i.get("thscode", "")).upper() for i in items
                  if "ST" in str(i.get("name") or "").upper()]
        offset += len(items)
        if len(items) < 1000:
            break
    pl.DataFrame({"symbol": sorted(set(found))}).write_parquet(out)
    return sorted(set(found))


def db_max_date(cfg: dict) -> str:
    """本地库里最新的行情日期（YYYY-MM-DD）。

    必须以它为分析窗口的锚：交易日历会包含"今天"，但今天盘后数据还没落库
    （盘中或开盘前跑分析时，库里最新只到上一个交易日）。

    优先读取本地 parquet 缓存（避免 DuckDB 内存限制）；若无缓存则回退 db export。
    """
    cache_dir = _cache_dir(cfg)
    candidates = []

    # 方案1：从最新的 panel parquet 文件名提取日期
    # 文件名格式: panel_2026-09-18_2026-09-23_qfq.parquet
    panels = sorted(cache_dir.glob("panel_*_qfq.parquet"))
    if panels:
        name = panels[-1].stem  # 去掉 .parquet
        # 格式: panel_2026-09-18_2026-09-23_qfq
        parts = name.split("_")
        # parts: ['panel', '2026-09-18', '2026-09-23', 'qfq']
        if len(parts) >= 3:
            candidates.append(parts[2])  # 取 end date

    # 方案2：从旧格式 parquet 文件名提取日期
    parquet_files = sorted(cache_dir.glob("qfq_*.parquet"))
    if parquet_files:
        name = parquet_files[-1].stem
        parts = name.split("_")
        if len(parts) >= 3:
            candidates.append(parts[-1])

    # 方案3：检查 market store 的 raw 文件（通达信盘后包同步）
    market_raw = _data_root(cfg) / "market" / "kline_daily_raw" / "_all.parquet"
    if market_raw.exists():
        try:
            import polars as pl
            df = pl.read_parquet(market_raw, columns=["date"])
            max_date = df["date"].max()
            if max_date is not None:
                candidates.append(str(max_date)[:10])
        except Exception:
            pass

    # 返回最新的日期
    if candidates:
        return max(candidates)

    # 方案4：回退到 db export（兼容旧缓存）
    df = export_sql(cfg, "SELECT max(date) AS d FROM v_daily", "db_max_date",
                    force=True, timeout=300)
    val = df["d"][0] if not df.is_empty() else None
    if val is None:
        raise RuntimeError("本地库里没有任何行情数据（先跑 data init / data sync）")
    return str(val)[:10]


def last_trading_day(cfg: dict) -> str:
    """分析用的最后一个交易日 = 本地库里的最新行情日。"""
    return db_max_date(cfg)


def local_trading_days(cfg: dict) -> list[str]:
    """本地库实际交易日（raw 日K日期集合，YYYY-MM-DD 升序）。

    远端 `calendar_days` 单次只覆盖约一年——历史长窗口（周期标签/回测）
    的预热取数用本函数，见 window_for_regime 的回退。
    """
    from .parquet_store import _store_dir
    p = _store_dir(cfg) / "kline_daily_raw" / "_all.parquet"
    df = pl.read_parquet(p, columns=["date"])
    return sorted({str(d)[:10] for d in df["date"].to_list()})


def window_for_regime(cfg: dict, days: int, *,
                      warmup: int = 40) -> tuple[str, str, str]:
    """情绪周期的取数窗口 → (预热起点, 目标起点, 终点)。

    指标（ma20 等）需要预热行才在目标区间开头就有值，所以取数要多往前留 `warmup`
    个交易日；聚合完再把预热段丢掉，只保留 [目标起点, 终点]。
    """
    end = db_max_date(cfg)
    cal = [d for d in calendar_days(cfg) if d <= end]
    need = days + warmup
    if len(cal) < need:
        # 远端日历只约一年：历史长窗口（周期标签回溯多年）回退本地库
        # 实际交易日；生产短窗口（days=60）日历够用，走原路径行为不变。
        local = [d for d in local_trading_days(cfg) if d <= end]
        if len(local) >= need:
            cal = local
    if len(cal) < need:
        raise RuntimeError(f"交易日历覆盖不足：需要 {need} 个交易日，只有 {len(cal)}")
    return cal[-need], cal[-days], end


def symbol_names(cfg: dict, *, force: bool = False) -> pl.DataFrame:
    """全市场代码 → 名称映射（两列：symbol_upper, name）。

    本地库 v_symbol.name 为空，名称只能走远端 `symbol list`（每页 ≤1000，约 6 页）。
    按天缓存到 name_map.parquet（更名/摘帽会变）。选股结果靠它显示名称。
    """
    out = _cache_dir(cfg) / "name_map.parquet"
    if not force and out.exists():
        age = datetime.now(timezone.utc).date() - datetime.fromtimestamp(
            out.stat().st_mtime, tz=timezone.utc).date()
        if age.days < 1:
            return pl.read_parquet(out)

    ht = _adapter(cfg)
    pairs: list[tuple[str, str]] = []
    offset = 0
    while True:
        res = ht.symbol_list(offset=offset, limit=1000)
        if res.status != models.OK:
            logger.warning("名称映射获取中断 offset=%s: %s", offset, res.status)
            break
        items = (res.data or {}).get("item") or []
        if not items:
            break
        for it in items:
            code = str(it.get("thscode") or "").strip().upper()
            name = (it.get("name") or "").strip()
            if code:
                pairs.append((code, name))
        offset += len(items)
        if len(items) < 1000:
            break

    df = pl.DataFrame({"symbol": [p[0] for p in pairs],
                       "name": [p[1] for p in pairs]}).unique(subset=["symbol"])
    if df.height == 0:
        # 远端失败/空结果绝不落缓存：空 list 在 polars 是 Null 型，一旦写入
        # 24h 内所有 join 名字的调用都会 SchemaError（2026-11-03 云上实测中毒）
        logger.warning("名称映射获取为空（远端失败？）——不写缓存直接返回")
        return df
    tmp = out.with_suffix(".part.parquet")
    df.write_parquet(tmp)
    tmp.replace(out)
    logger.info("名称映射: %d 只", df.height)
    return df


def attach_names(cfg: dict, frame: pl.DataFrame, *,
                 col: str = "symbol") -> pl.DataFrame:
    """给帧 join 上 name 列（frame[col] 可能是带后缀代码）。"""
    if "name" in frame.columns:
        return frame
    names = symbol_names(cfg)
    if names.height == 0 or names.schema.get("symbol") == pl.Null:
        return frame  # 名称缺失降级：不 join
    key = frame[col].cast(pl.Utf8).str.to_uppercase()
    f = frame.with_columns(key.alias("_sym_key"))
    f = f.join(names.rename({"symbol": "_sym_key"}), on="_sym_key", how="left")
    return f.drop("_sym_key")


__all__ = [
    "export_sql", "market_frame", "index_daily", "index_pct_map",
    "calendar_days", "st_symbols", "last_trading_day", "window_for_regime",
    "symbol_names", "attach_names",
]
