"""今日五维总览：不用指定个股，一次看全市场层面的各维状态。

- market：hithink 涨停/跌停池计数 + 连板梯队 + 本地情绪周期
- national_team：宽基 ETF 份额环比 + 最近披露期加减仓分布
- zsxq：最新纪要（日期 + 开头摘录）
- serenity：not_applicable（AI 侧技能）
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .. import models
from ..adapters.hithink import HithinkAdapter
from ..adapters.investment_news import InvestmentNewsAdapter
from ..adapters.national_team import NationalTeamAdapter
from ..adapters.zsxq import ZsxqAdapter
from .bundle import (_SERENITY_HINT, _combine_status, _combined_meta,
                     _unavailable_error)


def _market_dimension(cfg: dict) -> models.DimensionResult:
    ht = HithinkAdapter(cfg.get("hithink", {}))
    limit_up = ht.limit_pool("up", size=200)
    limit_down = ht.limit_pool("down", size=200)
    ladder = ht.limit_ladder()

    # 情绪周期走本地（hithink 全市场日线 → 指标 → 聚合 → 规则分类）。
    # 本地失败记结构化错误，不拖垮整个总览。
    from ..market import service as regime_service
    try:
        local_regime = regime_service.snapshot(cfg, days=60)
        regime_status = models.OK
    except Exception as exc:  # noqa: BLE001
        local_regime = {"hint": f"本地情绪周期计算失败：{exc}"}
        regime_status = models.UNAVAILABLE

    def _pool_count(res: models.DimensionResult):
        data = res.data
        if res.status in (models.OK, models.STALE) and isinstance(data, dict):
            total = data.get("total")
            if total is not None:
                return total
            items = data.get("item")
            if items is None:
                items = data.get("rows")
            if isinstance(items, list):
                return len(items)  # 空池要返回 0，不是 None
        return None

    all_results = [limit_up, limit_down, ladder,
                   models.DimensionResult(status=regime_status, source="local-regime")]
    status = _combine_status(all_results)
    data = {
        "local_regime": local_regime,
        "limit_up_pool_count": _pool_count(limit_up),
        "limit_down_pool_count": _pool_count(limit_down),
        "hithink_limit_up": limit_up.to_dict(),
        "hithink_limit_down": limit_down.to_dict(),
        "hithink_limit_ladder": ladder.to_dict(),
        "note": "limit_up/down_pool_count 是当日涨/跌停家数（短线情绪温度计）；"
                "limit_ladder 是连板梯队（情绪高度）；local_regime 是本地情绪周期"
                "（情绪分/状态/阶段）",
    }
    return models.DimensionResult(
        status=status, source="hithink+local-regime",
        meta=_combined_meta(all_results), data=data)


def _national_team_dimension(cfg: dict) -> models.DimensionResult:
    nt = NationalTeamAdapter(cfg.get("national_team", {}))
    etf = nt.etf_shares(weeks=8)
    changes = nt.recent_changes(limit=20)
    status = _combine_status([etf, changes])
    data = {
        "etf_shares": etf.to_dict(),
        "recent_changes": changes.to_dict(),
        "note": "etf_shares 是份额温度计（汇金宽基仓位代理，周频缺省回退日频）；"
                "recent_changes 是最近财报披露期的个股加减仓分布（季报粒度）",
    }
    err = _unavailable_error(
        [etf, changes], "国家队维度不可用（ETF 份额文件未配置且 PG 不可用）"
    ) if status == models.UNAVAILABLE else None
    return models.DimensionResult(
        status=status, source="national-team-json+national-team-pg",
        meta=_combined_meta([etf, changes]), data=data, error=err)


def _zsxq_dimension(cfg: dict, allow_zsxq: bool = True,
                    allow_news: bool = True) -> models.DimensionResult:
    """信息面：zsxq 最新纪要 + investment-news 全球产业要点。

    allow_zsxq/allow_news：内容裁剪闸（规格 §2.1 约束②——有会话但能力不足的
    账号不取不回传）。**缺省 True = 机主自用（CLI/MCP/无会话直调）视同有权限、
    不裁剪**；HTTP 侧 _content_kwargs 按会话账号能力显式传入（无会话 401）。
    """
    zx = ZsxqAdapter(cfg.get("zsxq", {})) if allow_zsxq else None
    nws = InvestmentNewsAdapter(cfg.get("investment_news", {})) if allow_news else None
    denied = models.DimensionResult(
        status=models.NOT_APPLICABLE, source="permission",
        meta=models.SourceMeta(note="无权限"),
        data={"note": "当前账号无该来源权限，不回传内容"})
    zx_res = zx.latest(excerpt_chars=1500) if zx else denied
    nw_res = nws.latest(max_items=3) if nws else denied
    if allow_zsxq or allow_news:
        active = [r for r in (zx_res, nw_res)
                  if r.status != models.NOT_APPLICABLE]
        status, meta = _combine_status(active), _combined_meta(active)
    else:
        status, meta = models.NOT_APPLICABLE, models.SourceMeta(
            note="无 zsxq/view_news 权限")
    data = {"zsxq_latest": zx_res.to_dict(), "news": nw_res.to_dict(),
            "note": "信息面：" + " + ".join(
                [s for s, on in
                 (("zsxq 付费纪要（预期差/催化）", allow_zsxq),
                  ("investment-news 12 赛道全球产业资讯（中文要点）", allow_news))
                 if on] or ["无 zsxq/view_news 权限，不回传内容"])}
    return models.DimensionResult(
        status=status, source="zsxq-files+investment-news",
        meta=meta, data=data)


def build_overview(cfg: dict, allow_zsxq: bool = True,
                   allow_news: bool = True) -> models.Envelope:
    """今日五维总览。allow_zsxq/allow_news：纪要与资讯内容闸——缺省 True
    （机主自用不裁剪），HTTP 侧由 _content_kwargs 按会话账号能力显式传入。"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_market = pool.submit(_market_dimension, cfg)
        f_nt = pool.submit(_national_team_dimension, cfg)
        f_zsxq = pool.submit(_zsxq_dimension, cfg, allow_zsxq, allow_news)

    env = models.Envelope(command="overview")
    env.dimensions = {
        "market": f_market.result(),
        "serenity": models.DimensionResult(
            status=models.NOT_APPLICABLE, source="serenity-skill",
            meta=models.SourceMeta(note="无数据服务"),
            data={"hint": _SERENITY_HINT}),
        "national_team": f_nt.result(),
        "zsxq": f_zsxq.result(),
    }
    return env
