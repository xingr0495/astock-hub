"""astock-hub MCP server（stdio）。

给 Claude Code 等 MCP 客户端提供 17 个工具，与 CLI 共用同一套适配器核心，
返回同样的 Envelope 结构（dict 形式）。

启动：python -m astock_hub.mcp_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import load_config
from . import models
from .core.bundle import build_bundle
from .core.kline import levels_dim, local_kline_dim
from .core.overview import build_overview
from .symbols import normalize
from .adapters.hithink import HithinkAdapter
from .adapters.investment_news import InvestmentNewsAdapter
from .adapters.national_team import NationalTeamAdapter
from .adapters.zsxq import ZsxqAdapter

mcp = FastMCP("astock-hub")

_cfg: dict | None = None


def _get_cfg() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


_FRESHNESS_RULE = (
    "注意：每个维度的 status 为 stale/unavailable 时表示数据陈旧或缺失，"
    "禁止当作有效信号参与五维共振计数；error.hint 是给人看的排错步骤，原样转述给用户。"
)


@mcp.tool(description="分析前把数据刷新到最新：默认快速四源（etf 国家队ETF份额/zsxq 原始帖子/tdx_daily 通达信盘后落库/news investment-news 全球产业资讯，其中 news 要跑大模型出中文要点，耗时几分钟）。sources 逗号分隔，可选 etf,zsxq,news,national_team,hithink_data,tdx_daily,all；national_team 是长任务（10分钟级）需显式指定。刷完建议再调 astock_health 确认。")
def astock_refresh(sources: str = "", force: bool = False) -> dict:
    from .refresh import ALL_SOURCES, QUICK_SOURCES, run_refresh
    if sources:
        src = ([s.strip() for s in sources.split(",") if s.strip()]
               if sources != "all" else ALL_SOURCES)
    else:
        src = QUICK_SOURCES
    return run_refresh(_get_cfg(), sources=src, force=force).to_dict()


@mcp.tool(description="四路数据源健康检查：国家队/zsxq/hithink/investment-news 各自可用性与数据新鲜度。分析前建议先调。" + _FRESHNESS_RULE)
def astock_health() -> dict:
    cfg = _get_cfg()
    results = {
        "national_team": NationalTeamAdapter(cfg.get("national_team", {})).health(),
        "zsxq": ZsxqAdapter(cfg.get("zsxq", {})).health(),
        "hithink": HithinkAdapter(cfg.get("hithink", {})).health(),
        "investment_news": InvestmentNewsAdapter(
            cfg.get("investment_news", {})).health(),
    }
    return {name: r.to_dict() for name, r in results.items()}


@mcp.tool(description="个股五维数据包：一次拿齐行情(a-stock-data+hithink)/国家队资金/zsxq信息 三个维度的数据（serenity 维度需调 serenity-skill 技能）。" + _FRESHNESS_RULE)
def astock_bundle(symbol: str, days: int = 120) -> dict:
    """symbol: 6 位代码（如 600519）或股票名称；days: 日K天数"""
    return build_bundle(_get_cfg(), symbol, days=days).to_dict()


@mcp.tool(description="今日五维总览：市场层面的行情/情绪周期/涨停池/连板梯队/国家队ETF仓位/zsxq最新纪要，不用指定个股。" + _FRESHNESS_RULE)
def astock_overview() -> dict:
    return build_overview(_get_cfg()).to_dict()


@mcp.tool(description="单股日K线 + 技术指标（MA/EMA/MACD/BOLL/KDJ/RSI/ATR/量比/60日高低，前复权）。**本地计算**。")
def astock_kline(symbol: str, days: int = 120) -> dict:
    cfg = _get_cfg()
    try:
        return local_kline_dim(cfg, normalize(symbol), days).to_dict()
    except Exception as exc:  # noqa: BLE001
        return models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-indicators",
            data={"hint": f"本地日K计算失败：{exc}"}).to_dict()


@mcp.tool(description="个股关键价位（11 类）：成交密集区/POC、枢轴点、前高前低、布林带、Keltner 三档、ATR 止损、缺口、斐波那契、整数关口，附一段支撑压力摘要。本地计算。")
def astock_levels(symbol: str, days: int = 250) -> dict:
    """symbol: 6 位代码（如 600519）或股票名称；days: 取多少根日K算价位"""
    cfg = _get_cfg()
    try:
        return levels_dim(cfg, normalize(symbol), days).to_dict()
    except Exception as exc:  # noqa: BLE001
        return models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-levels",
            data={"hint": f"关键价位计算失败：{exc}"}).to_dict()


@mcp.tool(description="每日市场主线排行（概念+行业，基于涨停梯队：概念内涨停家数/最高连板/梯队完整度加权，含龙头股）。**本地计算**（同花顺全市场日线+概念成分）。成分是当前快照回看历史，早年归属漂移，越近越准。days: 展示多少天；refresh: 是否重算（首次含成分构建，较慢）。")
def astock_mainline(days: int = 30, refresh: bool = False) -> dict:
    from .market import service as market_service
    try:
        return models.DimensionResult(
            status=models.OK, source="hithink+local-mainline",
            data=market_service.mainline_snapshot(
                _get_cfg(), days=days, refresh=refresh)).to_dict()
    except Exception as exc:  # noqa: BLE001
        return models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-mainline",
            data={"hint": f"主线计算失败：{exc}"}).to_dict()


@mcp.tool(description="板块涨幅轮动矩阵（概念+行业）：每个交易日各自把板块按当天平均涨幅从高到低排列，看资金在板块间如何轮动。**本地计算**。days: 7-30 天（默认 12）；refresh: 是否重建概念成分映射。")
def astock_rps(days: int = 12, refresh: bool = False) -> dict:
    from .market import service as market_service
    try:
        return models.DimensionResult(
            status=models.OK, source="hithink+local-rps",
            data=market_service.rps_snapshot(
                _get_cfg(), days=days, refresh=refresh)).to_dict()
    except Exception as exc:  # noqa: BLE001
        return models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-rps",
            data={"hint": f"板块轮动计算失败：{exc}"}).to_dict()


@mcp.tool(description="运行内置选股策略，返回 as_of 当日满足入场条件的股票及打分排名（本地全市场计算）。strategy: 策略 id（先用 astock_strategies 查看可用清单，如 ma_golden_cross/boll_breakout/near_limit_up/consecutive_limit_ups 等 25 个）；limit: 返回条数。注：high_turnover_surge 需当日换手率/流通股本，本地历史库不含股本，会返回 unavailable。")
def astock_screen(strategy: str, limit: int | None = None) -> dict:
    from .market import service as market_service
    try:
        return models.DimensionResult(
            status=models.OK, source="hithink+local-screen",
            data=market_service.screen_strategy(
                _get_cfg(), strategy, limit=limit)).to_dict()
    except Exception as exc:  # noqa: BLE001
        return models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-screen",
            data={"hint": f"选股失败：{exc}"}).to_dict()


@mcp.tool(description="列出 25 个内置选股策略的目录：id、名称、描述、标签、可调参数。配合 astock_screen 使用。")
def astock_strategies() -> dict:
    from .market import service as market_service
    return models.DimensionResult(
        status=models.OK, source="local",
        data={"strategies": market_service.list_available_strategies()}).to_dict()


@mcp.tool(description="市场环境（情绪周期）：情绪分 0-100 + 5 档状态（强势/偏强/震荡/偏弱/弱势）+ 6 阶段（冰点/启动/主升/高潮/退潮/修复）+ 赚钱/投机/抗跌/趋势四个子维度分 + 近 N 日时序与最近一次阶段切换。**本地计算**（同花顺全市场日线）。")
def astock_market_regime(days: int = 60, refresh: bool = False) -> dict:
    """days: 返回多少天的时序（默认 60）；refresh: 是否重算目标区间（全市场，约几秒）"""
    from .market import service as regime_service
    try:
        snap = regime_service.snapshot(_get_cfg(), days=days, refresh=refresh)
        return models.DimensionResult(
            status=models.OK, source="hithink+local-regime",
            data=snap).to_dict()
    except Exception as exc:  # noqa: BLE001
        return models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-regime",
            data={"hint": f"情绪周期计算失败：{exc}"}).to_dict()


@mcp.tool(description="国家队（汇金/证金/社保/养老金）在指定个股上的持仓结论：持仓量、估算成本、盈亏、加减仓文案。季报粒度；未覆盖≠没买（十大股东披露有门槛）。")
def astock_nt_position(symbol: str) -> dict:
    cfg = _get_cfg()
    return NationalTeamAdapter(cfg.get("national_team", {})).position(
        normalize(symbol).ts_code).to_dict()


@mcp.tool(description="中央汇金宽基 ETF 份额序列（周频温度计：六大宽基 ETF 份额变化≈汇金净申购/赎回），含环比。")
def astock_nt_etf_shares(weeks: int = 12) -> dict:
    cfg = _get_cfg()
    return NationalTeamAdapter(cfg.get("national_team", {})).etf_shares(weeks).to_dict()


@mcp.tool(description="investment-news 各赛道最新动向：12 大赛道（AI/半导体/机器人/新能源车…对应 A 股板块）的全球产业新闻 + AI 中文今日要点。industry 可选单个赛道 key。")
def astock_news_latest(industry: str = "") -> dict:
    cfg = _get_cfg()
    return InvestmentNewsAdapter(cfg.get("investment_news", {})).latest(
        industry=industry or None).to_dict()


@mcp.tool(description="在 investment-news 的全球产业新闻里搜索关键词（中英文标题/摘要/来源），返回各赛道命中条目。")
def astock_news_search(keyword: str, days: int = 7) -> dict:
    cfg = _get_cfg()
    return InvestmentNewsAdapter(cfg.get("investment_news", {})).search(
        keyword, days=days).to_dict()


@mcp.tool(description="最新一份知识星球（调研鹅）纪要：元信息 + 开头摘录，全文路径在返回里。")
def astock_zsxq_latest() -> dict:
    cfg = _get_cfg()
    return ZsxqAdapter(cfg.get("zsxq", {})).latest().to_dict()


@mcp.tool(description="在近期知识星球纪要里搜索关键词（股票名/代码/主题），返回命中段落的上下文。纪要里通常写股票名称，建议优先用名称搜。")
def astock_zsxq_search(keyword: str, days: int = 7) -> dict:
    cfg = _get_cfg()
    return ZsxqAdapter(cfg.get("zsxq", {})).search(keyword, days=days).to_dict()


def main() -> None:
    mcp.run()  # stdio 传输


if __name__ == "__main__":
    main()
