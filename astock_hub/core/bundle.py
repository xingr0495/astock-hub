"""个股五维数据包：一次调用拿齐五维数据。

五维与数据源映射：
- market（行情）：a-stock-data 为主（腾讯K线 + 腾讯快照 + 东财主力资金流），
  hithink 快照互备（2026-09-24 起 a-stock-data 是行情主数据源）
- serenity（产业链）：AI 侧技能，hub 固定返回 not_applicable + 提示
- national_team（资金面）：PG 个股持仓结论 + 宽基 ETF 份额
- zsxq（信息面）：纪要关键词搜索 + 最新纪要信息

每维独立成败；stale/unavailable 的维度禁止参与共振计数（技能侧规则）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import models
from ..symbols import SymbolInfo, is_code_like, normalize
from ..adapters.astock_data import AStockDataAdapter
from ..adapters.hithink import HithinkAdapter
from ..adapters.investment_news import InvestmentNewsAdapter
from ..adapters.national_team import NationalTeamAdapter
from ..adapters.zsxq import ZsxqAdapter

_SERENITY_HINT = (
    "serenity 是 AI 侧研究技能，没有数据服务可聚合。"
    "请调用 serenity-skill 技能完成产业链深度研究这一维度"
)


def _combine_status(results: list[models.DimensionResult]) -> str:
    statuses = {r.status for r in results}
    if statuses == {models.SKIPPED}:
        return models.SKIPPED
    good = statuses & {models.OK, models.STALE, models.PARTIAL, models.NOT_FOUND}
    if not good:
        return models.UNAVAILABLE
    if statuses - good:
        # 一部分可用、一部分挂了
        return models.PARTIAL
    if models.OK in statuses:
        return models.OK
    if models.STALE in statuses:
        return models.STALE
    return models.NOT_FOUND


def _combined_meta(results: list[models.DimensionResult]) -> models.SourceMeta:
    as_ofs = [r.meta.as_of for r in results if r.meta.as_of]
    kinds = [r.meta.freshness_kind for r in results]
    meta = models.SourceMeta(
        as_of=max(as_ofs) if as_ofs else None,
        freshness_kind=kinds[0] if kinds else "daily",
    )
    return meta


def resolve_symbol(cfg: dict, query: str) -> tuple[SymbolInfo | None, str | None, str | None]:
    """把用户输入（代码或名称）解析成 (SymbolInfo, 名称, 名称来源)。"""
    query = query.strip()
    if is_code_like(query):
        sym = normalize(query)
        # 顺带用 hithink 搜个中文名（失败不阻塞）
        items = HithinkAdapter(cfg.get("hithink", {})).search(sym.bare, limit=1)
        if items:
            return sym, items[0].get("name"), "hithink"
        return sym, None, None
    # 名称输入：hithink 搜索消歧
    items = HithinkAdapter(cfg.get("hithink", {})).search(query, limit=1)
    if items:
        return normalize(items[0]["thscode"]), items[0].get("name"), "hithink"
    return None, query, None


def _market_dimension(cfg: dict, sym: SymbolInfo, days: int) -> models.DimensionResult:
    """行情维度：a-stock-data 主源（K线/快照/主力资金流）+ hithink 快照互备。"""
    asd = AStockDataAdapter(cfg.get("a_stock_data", {}))
    ht = HithinkAdapter(cfg.get("hithink", {})).snapshot([sym.ts_code])

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_kline = pool.submit(asd.tencent_kline, sym.ts_code,
                              count=min(max(days, 20), 640))
        f_quote = pool.submit(asd.tencent_quote, [sym.bare])
        f_flow = pool.submit(asd.fund_flow_daily, sym.ts_code,
                             days=min(max(days, 20), 120))
        kline = f_kline.result()
        quote = f_quote.result()
        flow = f_flow.result()

    results = [kline, quote, flow, ht]
    status = _combine_status(results)
    data = {
        "sources": {
            "a_stock_data_kline": kline.to_dict(),
            "a_stock_data_quote": quote.to_dict(),
            "a_stock_data_fund_flow": flow.to_dict(),
            "hithink": ht.to_dict(),
        },
        "note": "行情主源 a-stock-data：腾讯K线（前复权）+ 腾讯实时快照 + "
                "东财主力资金流（日级，主力/超大单/大单净流入）；"
                "hithink 同花顺快照作互备交叉验证",
    }
    return models.DimensionResult(
        status=status, source="a-stock-data+hithink-cli",
        meta=_combined_meta(results), data=data)


def _unavailable_error(subs, fallback_msg: str) -> dict:
    """聚合维 UNAVAILABLE 的错误说明（Envelope 契约要求）：
    优先取子结果的错误，兜底给统一文案。"""
    for sub in subs:
        sub_err = sub.to_dict().get("error")
        if sub_err:
            return sub_err
    return {"code": "UNAVAILABLE", "message": fallback_msg, "retryable": False}


def _national_team_dimension(cfg: dict, sym: SymbolInfo) -> models.DimensionResult:
    nt = NationalTeamAdapter(cfg.get("national_team", {}))
    pos = nt.position(sym.ts_code)
    etf = nt.etf_shares(weeks=8)
    status = _combine_status([pos, etf])
    data = {
        "position": pos.to_dict(),
        "etf_shares": etf.to_dict(),
        "note": "position 回答「国家队在这只个股上的持仓/加减仓」（季报粒度，未覆盖≠没买）；"
                "etf_shares 回答「汇金在宽基 ETF 上的整体仓位变化」（份额温度计，周频缺省回退日频）",
    }
    # 契约：UNAVAILABLE 必须带错误说明（云上 ETF 未配置+PG 停用曾聚合出裸 UNAVAILABLE）
    err = _unavailable_error(
        [pos, etf], "国家队维度不可用（PG 未启用且 ETF 份额文件未配置）"
    ) if status == models.UNAVAILABLE else None
    return models.DimensionResult(
        status=status, source="national-team-pg+national-team-json",
        meta=_combined_meta([pos, etf]), data=data, error=err)


def _news_related(cfg: dict, sym: SymbolInfo) -> dict:
    """按个股行业/概念映射产业资讯赛道，取相关赛道要点+最新新闻。

    全球产业新闻正文几乎不出现 A 股简称，按公司名搜常年零命中；改走
    news_pipe.related 的赛道映射。任何取数失败返回空结构（叠加层不炸信息面）。
    """
    empty = {"tracks": [], "matched_terms": []}
    try:
        from ..news_pipe.related import match_tracks, track_name
        from ..store.concepts import membership_lookup
        concepts = membership_lookup(cfg, "concept").get(sym.ts_code, [])
        industries = membership_lookup(cfg, "industry").get(sym.ts_code, [])
        industry = industries[0] if industries else None
        keys = match_tracks(industry, concepts)
        if not keys:
            return empty
        nws = InvestmentNewsAdapter(cfg.get("investment_news", {}))
        tracks = []
        for key in keys:
            dim = nws.latest(industry=key, max_items=3)
            if dim.status != models.OK:
                continue
            payload = (dim.data or {}).get("industries") or []
            if not payload:
                continue
            blk = payload[0]
            tracks.append({"key": key, "name": track_name(key) or blk.get("name"),
                           "points": blk.get("points") or [],
                           "top_items": blk.get("top_items") or []})
        return {"tracks": tracks,
                "matched_terms": [industry] + concepts if industry else concepts}
    except Exception:  # noqa: BLE001 — 资讯叠加失败不影响纪要等信息面主体
        return empty


def _denied(subject: str, empty_key: str, empty_val) -> models.DimensionResult:
    """权限裁剪占位（规格 §2.1 约束②：无能力就不回传内容）。

    形态：键保留、内容置空 + status=not_applicable——前端各段落都是
    判空渲染（hits/.tracks/excerpt 缺省走 []），不破坏既有消费点。
    """
    return models.DimensionResult(
        status=models.NOT_APPLICABLE, source="permission",
        meta=models.SourceMeta(note=f"无 {subject} 权限"),
        data={empty_key: empty_val,
              "note": f"当前账号无 {subject} 权限，不回传内容"})


def _zsxq_dimension(cfg: dict, sym: SymbolInfo, name: str | None,
                    user: str | None = None,
                    is_admin: bool = False,
                    allow_zsxq: bool = True,
                    allow_news: bool = True) -> models.DimensionResult:
    """信息面维度 = zsxq 纪要 + investment-news 全球产业资讯，双源叠加。

    user/is_admin：本地资料腿只扫当前用户的目录（不传走旧全局根）。
    allow_zsxq/allow_news：内容裁剪闸。**缺省 True = 机主自用（CLI/MCP/无会话
    直调）视同有权限、不裁剪**；只有 HTTP 侧 _content_kwargs 按会话账号能力
    显式传 False 才裁剪（生产 HTTP 必有会话用户，无会话 401）。
    """
    keyword = name or sym.bare
    zx = ZsxqAdapter(cfg.get("zsxq", {})) if allow_zsxq else None
    nws = InvestmentNewsAdapter(cfg.get("investment_news", {})) if allow_news else None
    search = (_denied("zsxq", "hits", []) if zx is None else
              zx.search(keyword, days=7))
    latest = (_denied("zsxq", "reports", []) if zx is None else
              zx.latest(excerpt_chars=500))
    news_search = (_denied("view_news", "hits", []) if nws is None else
                   nws.search(keyword, max_hits=6))
    news_latest = (_denied("view_news", "industries", []) if nws is None else
                   nws.latest(max_items=3))
    news_related = (_news_related(cfg, sym) if allow_news else
                    {"tracks": [], "matched_terms": [],
                     "note": "当前账号无 view_news 权限，不回传内容"})
    try:
        from ..adapters.local_docs import LocalDocsAdapter
        local_search = LocalDocsAdapter(cfg, user=user, is_admin=is_admin).search(
            keyword, days=30, max_hits=4)
    except ImportError:  # 当前仓库不包含本地资料模块
        local_search = None
    legs = [search, latest, news_search, news_latest]
    if local_search is not None:
        legs.append(local_search)
    if allow_zsxq or allow_news:
        active = [r for r in legs if r.status != models.NOT_APPLICABLE]
        status = _combine_status(active)
        meta = _combined_meta(active)
    else:
        # 纪要与资讯两腿都被裁剪（会话账号两项能力都无）：整维标 not_applicable
        # （本地资料腿仍回传自己的）
        status, meta = models.NOT_APPLICABLE, models.SourceMeta(
            note="无 zsxq/view_news 权限")
    parts = []
    if allow_zsxq:
        parts.append("zsxq（付费纪要，写名称、预期差与催化）")
    if allow_news:
        parts.append("investment-news（12 赛道全球产业资讯，按公司行业/概念"
                     "映射相关赛道，出中文要点与个股线索）")
    if local_search is not None:
        parts.append("本地资料（用户上传文档的全文命中）")
    data = {
        "keyword": keyword,
        "zsxq_mentions": search.to_dict(),
        "zsxq_latest_report": latest.to_dict(),
        "news_mentions": news_search.to_dict(),
        "news_today": news_latest.to_dict(),
        "news_related": news_related,
        "local_docs_mentions": (local_search.to_dict() if local_search is not None else {
            "status": "not_applicable", "data": {"hits": [], "note": "当前仓库未包含本地资料模块"}}),
        "note": "信息面：" + " + ".join(parts),
    }
    if not name:
        data["note"] += "；未能解析股票名称，仅按代码搜索（纪要/新闻里通常写名称，可能漏检）"
    return models.DimensionResult(
        status=status, source="zsxq-files+investment-news",
        meta=meta, data=data)


def _serenity_dimension() -> models.DimensionResult:
    return models.DimensionResult(
        status=models.NOT_APPLICABLE, source="serenity-skill",
        meta=models.SourceMeta(note="无数据服务"),
        data={"hint": _SERENITY_HINT})


def build_bundle(cfg: dict, query: str, days: int = 120,
                 user: str | None = None,
                 is_admin: bool = False,
                 allow_zsxq: bool = True,
                 allow_news: bool = True) -> models.Envelope:
    """个股五维数据包。user/is_admin 透传给信息面维度的本地资料腿
    （HTTP 口传当前请求用户；CLI/MCP 等不传走旧全局根）。
    allow_zsxq/allow_news：纪要与资讯内容闸——缺省 True（机主自用不裁剪），
    HTTP 侧由 _content_kwargs 按会话账号能力显式传入。"""
    sym, name, name_source = resolve_symbol(cfg, query)
    if sym is None:
        env = models.Envelope(command="bundle", query={"input": query})
        env.dimensions = {
            "resolve": models.DimensionResult(
                status=models.UNAVAILABLE, source="hithink-cli",
                error={"code": "NOT_FOUND",
                       "message": f"无法把「{query}」解析成 A 股代码",
                       "hint": "请换 6 位代码（如 600519）或更准确的名称重试",
                       "retryable": False})
        }
        return env

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_market = pool.submit(_market_dimension, cfg, sym, days)
        f_nt = pool.submit(_national_team_dimension, cfg, sym)
        f_zsxq = pool.submit(_zsxq_dimension, cfg, sym, name, user, is_admin,
                             allow_zsxq, allow_news)

    # 股票名补充：hithink 没搜到时，尝试腾讯快照返回的 name
    market = f_market.result()
    if not name:
        tq = (market.data or {}).get("sources", {}).get("a_stock_data_quote", {})
        items = (tq.get("data") or {}).get("item") or []
        if items and items[0].get("name"):
            name = items[0]["name"]
            name_source = "a-stock-data/tencent"

    env = models.Envelope(
        command="bundle",
        query={"input": query, "code": sym.bare, "ts_code": sym.ts_code,
               "name": name, "name_source": name_source},
    )
    env.dimensions = {
        "market": market,
        "serenity": _serenity_dimension(),
        "national_team": f_nt.result(),
        "zsxq": f_zsxq.result(),
    }
    return env
