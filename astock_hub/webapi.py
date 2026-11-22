"""astock-hub Web API —— 薄 HTTP 层，供前端面板直接查询。

与 CLI / MCP 共享同一套适配器与 Envelope 结构；本模块只把命令翻译成
HTTP 端点，不重复实现任何数据逻辑。响应统一为 models.Envelope.to_dict()
（或单源的 DimensionResult.to_dict()），单路数据源挂了也照常返回结构化
错误（status=unavailable + error.hint 供前端原样转述）。

启动（开发）：
  python -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756
生产模式：前端构建到 frontend/dist 后由本应用直接托管（单进程单端口）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import polars as pl

from . import models
from .config import PROJECT_ROOT, load_config
from .core.bundle import build_bundle
from .market.sessions import market_session
try:
    from .pool import build_pool, build_topics
except ImportError:  # 免费版不包含产业链选股模块
    build_pool = build_topics = None
from .refresh import ALL_SOURCES, QUICK_SOURCES, run_refresh
from .symbols import normalize

API_PREFIX = "/api"
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@lru_cache(maxsize=1)
def _get_cfg() -> dict:
    return load_config()


@lru_cache(maxsize=1)
def _get_adapters() -> dict:
    cfg = _get_cfg()
    from .adapters.hithink import HithinkAdapter
    from .adapters.investment_news import InvestmentNewsAdapter
    from .adapters.national_team import NationalTeamAdapter
    from .adapters.tushare import TushareAdapter
    from .adapters.zsxq import ZsxqAdapter
    return {
        "national_team": NationalTeamAdapter(cfg.get("national_team", {})),
        "zsxq": ZsxqAdapter(cfg.get("zsxq", {})),
        "hithink": HithinkAdapter(cfg.get("hithink", {})),
        "investment_news": InvestmentNewsAdapter(cfg.get("investment_news", {})),
        "tushare": TushareAdapter(cfg.get("tushare", {})),
    }


def _json(data, status_code: int = 200) -> JSONResponse:
    # status_code 可选：ta/analyze 等既有调用已传 400（此前签名不含该参数，
    # 一旦命中会 TypeError——T6 实现 /api/chat 时补齐，行为与调用方预期一致）
    return JSONResponse(content=data, status_code=status_code)


def _content_kwargs(request: Request) -> dict:
    """信息面内容裁剪参数（规格 §2.1 约束②：访客默认只能看行情）。

    按当前账号的 zsxq / view_news 能力给 build_bundle/build_pool
    传 allow_* —— 有会话但能力不足的用户不取不回传纪要与资讯内容
    （admin 恒真不受影响；测试旁路的合成账号是管理员 → 恒开）。
    **user is None = 机主自用（非 HTTP/无会话场景，如 CLI/MCP 直调）视同有权限
    放行**——本机工具链没有"访客"概念，裁掉会打残 a-stock-research 的五维信息面；
    生产 HTTP 路径中间件保证必有会话用户（无会话 401），None 在 HTTP 下不可达。
    注意 has_perm(None) 返回 False，必须先判 None 再落 has_perm。
    """
    u = getattr(request.state, "user", None)
    return {"allow_zsxq": u is None or _auth.has_perm(u, "zsxq"),
            "allow_news": u is None or _auth.has_perm(u, "view_news")}


def _web_bundle_payload(code: str, days: int, user: str | None = None,
                        is_admin: bool = False,
                        allow_zsxq: bool = True,
                        allow_news: bool = True) -> dict:
    """个股数据包（Web 口径）：重算状态汇总。

    user/is_admin 透传进 build_bundle（本地资料腿按用户隔离）；
    allow_zsxq/allow_news 是纪要与资讯内容闸（HTTP 侧由 _content_kwargs 按
    会话能力显式传入；缺省 True = 机主自用语义，见 _content_kwargs 注释）。
    """
    payload = build_bundle(_get_cfg(), code, days=days,
                           user=user, is_admin=is_admin,
                           allow_zsxq=allow_zsxq,
                           allow_news=allow_news).to_dict()
    dims = payload.get("dimensions") or {}
    counts: dict[str, int] = {}
    for d in dims.values():
        status = d.get("status")
        if status:
            counts[status] = counts.get(status, 0) + 1
    payload["summary"] = counts
    return payload


# ---------------------------------------------------------------- FastAPI app

app = FastAPI(title="Mind AShare Web API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """HTML 入口一律 no-cache：SPA 换构建哈希后，浏览器必须能拿到新 index.html，
    否则旧标签页永远跑旧 JS（2026-10-08 实测：按钮版构建用户端迟迟看不到）。"""
    response = await call_next(request)
    if "text/html" in (response.headers.get("content-type") or ""):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ------------------------------------------------------- 认证闸门（多账号）
# 单点权限映射表：路由 → "public" | "auth"(任意登录) | "admin" | 权限键。
# 测试旁路 auth._TEST_BYPASS 仅供 pytest conftest 打开，生产代码路径无开关。

from . import auth as _auth

_AUTH_PUBLIC = {
    f"{API_PREFIX}/health",
    f"{API_PREFIX}/license/verify",
    f"{API_PREFIX}/auth/session",
    f"{API_PREFIX}/auth/bootstrap-status",
    f"{API_PREFIX}/auth/bootstrap",
    f"{API_PREFIX}/auth/register",
    f"{API_PREFIX}/auth/login",
}
_AUTH_ADMIN_PREFIXES = (
    f"{API_PREFIX}/notify", f"{API_PREFIX}/alert",
    f"{API_PREFIX}/tq", f"{API_PREFIX}/gm",
    f"{API_PREFIX}/ta/", f"{API_PREFIX}/serenity/", f"{API_PREFIX}/refresh",
    f"{API_PREFIX}/market/update", f"{API_PREFIX}/accounts",
)
_AUTH_PERM_PREFIXES = (
    (f"{API_PREFIX}/local-docs", "local_docs"),
    (f"{API_PREFIX}/watchlist", "watchlist"),
    (f"{API_PREFIX}/chat", "ai_chat"),
    (f"{API_PREFIX}/strategy/ai-status", "ai_chat"),
    (f"{API_PREFIX}/zsxq", "zsxq"),            # 覆盖 zsxq-latest 与 /api/zsxq/* 纪要内容/汇总
    (f"{API_PREFIX}/digest-pool", "zsxq"),
    (f"{API_PREFIX}/news", "view_news"),
    (f"{API_PREFIX}/review", "view_review"),
    (f"{API_PREFIX}/pool", "pool_screen"),     # 产业链选股（2026-11-02 用户要求移入权限）
)


def _auth_classify(method: str, path: str) -> str:
    """路由 → "public" | "auth"(任意登录) | "admin" | 权限键 | "any:k1,k2"。
    单点权限映射表。"""
    if path in _AUTH_PUBLIC:
        return "public"
    if path == f"{API_PREFIX}/digest-pool/push":
        # push 保留 admin（外发飞书，真实副作用）。rebuild 2026-11-05 降 zsxq：
        # 重建只是读本地已存档纪要重算池（无网络/无 LLM），与 GET/DELETE 同域
        # 同权——日常全功能账号（a123456，zsxq=true 非 admin）在云端点重建
        # 被 admin 闸拦成 forbidden（用户报障）。
        return "admin"
    if path == f"{API_PREFIX}/nav-config":
        return "auth" if method.upper() == "GET" else "admin"
    if path == f"{API_PREFIX}/dashboard-layout":
        return "auth" if method.upper() == "GET" else "admin"
    # 模拟盘/回测（2026-11-05 权限化）：读走权限档，写仍仅管理员。
    # 模拟盘台账是全局共享状态——非管理员只读铁律，开户/冻结/重置/结算/
    # 队列删除/手动候选一律 admin；回测删除共享结果同守 admin，运行（计算任务）
    # 随 backtest 档放行。
    if path.startswith(f"{API_PREFIX}/paper"):
        return "paper_trading" if method.upper() == "GET" else "admin"
    if path.startswith(f"{API_PREFIX}/backtest"):
        if method.upper() == "DELETE":
            return "admin"
        return "backtest"
    if path.startswith(f"{API_PREFIX}/auth/"):
        return "auth"                      # logout 等
    for p in _AUTH_ADMIN_PREFIXES:
        if path.startswith(p):
            return "admin"
    # 凭证两页开放给访客（2026-11-02 用户拍板"数据源和大模型可以开放，
    # 但需要自行填写密钥"）：数据源/大模型各一档；总览与连通测试两页共用
    # → 任一档即可；未知子路径回 admin（fail-closed）
    if path == f"{API_PREFIX}/credentials" or path == f"{API_PREFIX}/credentials/test":
        return "any:data_sources,llm_settings"
    if path.startswith(f"{API_PREFIX}/credentials/source"):
        return "data_sources"
    if path.startswith(f"{API_PREFIX}/credentials/llm"):
        return "llm_settings"
    if path.startswith(f"{API_PREFIX}/credentials"):
        return "admin"
    for p, key in _AUTH_PERM_PREFIXES:
        if path.startswith(p):
            return key
    return "view_market"                   # 默认档：新行情接口自动落这里


@app.middleware("http")
async def _auth_gate(request, call_next):
    # 免费版为机主单用户模式；公网访问由部署层白名单保护。
    request.state.user = _auth.service_user()
    return await call_next(request)


@app.on_event("startup")
def _materialize_service_token():
    """服务令牌文件预生成（任务 13）。

    链脚本是"先读文件、后发请求"：面板不预生成的话，文件永远等不到第一次
    service_token() 调用（check_service_token 缺文件恒 False、空头不进检查）
    → 令牌机制首启即死锁。预生成失败不阻断面板启动（令牌路径 fail-closed）。
    """
    try:
        _auth.service_token()
    except Exception:  # noqa: BLE001 — 预生成失败不阻断面板启动
        pass

# 注册大盘复盘路由
from .webapi_review import router as review_router
app.include_router(review_router)

# 主线手册推送（构建/判定/发送在 manual_push；本文件只挂 HTTP 与 startup 调度）
try:
    from . import manual_push as _manual_push
except ImportError:  # 免费版：manual_push 已剔除
    _manual_push = None

# 注册财务报表与龙虎榜路由
from .webapi_financial import router as financial_router
app.include_router(financial_router)


# 免费版不注册实盘交易、模拟盘、回测或多账号路由。


@app.get(f"{API_PREFIX}/auth/session")
def api_auth_session_owner():
    return _json({"user": {"id": "owner", "username": "owner",
                           "is_admin": True, "perms": {}},
                  "need_bootstrap": False, "features": {}})


@app.get(f"{API_PREFIX}/rotation-analysis/stream")
def api_rotation_analysis_stream(request: Request, days: int = 12, kind: str = "concept"):
    """AI 板块轮动研判（B1-3）：五信号预计算 → LLM 流式输出（NDJSON delta/done）。"""
    from .llm_client import stream_llm
    from .market.rotation_analysis import build_messages, rotation_signals

    user = getattr(request.state, "user", None)
    cfg = _get_cfg()
    try:
        signals = rotation_signals(cfg, days=days, kind=kind)
    except Exception as exc:  # noqa: BLE001
        return _json({"type": "error", "message": f"信号计算失败：{exc}"})

    def gen():
        import json as _j
        try:
            for text in stream_llm(cfg, build_messages(signals), user=user):
                yield _j.dumps({"type": "delta", "text": text}, ensure_ascii=False) + "\n"
            yield _j.dumps({"type": "done"}, ensure_ascii=False) + "\n"
        except Exception as exc:  # noqa: BLE001
            yield _j.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get(f"{API_PREFIX}/rotation-replay")
def api_rotation_replay(kind: str = Query("concept", pattern="^(concept|industry)$"),
                        bucket: int = Query(5),
                        day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """盘中板块切换·分钟回放（TSP 核心口径：分钟矩阵 + 1小时换血率）。"""
    from .market.rotation_replay import rotation_replay
    if bucket not in (1, 5, 15):
        bucket = 5
    try:
        return _json(rotation_replay(_get_cfg(), kind, bucket, day))
    except Exception as exc:  # noqa: BLE001 — 辅助图失败不拖垮板块页
        return _json({"status": "error", "available": False, "error": str(exc)})


@app.get(f"{API_PREFIX}/health")
def api_health():
    """五路数据源健康检查（含新鲜度）。"""
    adapters = _get_adapters()
    results = {name: adapter.health() for name, adapter in adapters.items()}
    env = models.Envelope(command="health")
    env.data = {name: r.to_dict() for name, r in results.items()}
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/bundle")
def api_bundle(request: Request, code: str,
               days: int = Query(120, ge=20, le=500)):
    """个股数据包（行情/产业链/国家队/信息面；DSA 已停用不展示）。

    信息面·本地资料腿按当前请求用户取数（透传 user/is_admin）；
    纪要/资讯内容按当前账号能力裁剪（无权限不回传）。
    """
    return _json(_web_bundle_payload(
        code, days, **_request_user_kwargs(request), **_content_kwargs(request)))


@app.get(f"{API_PREFIX}/kline/tencent")
def api_kline_tencent(code: str, days: int = Query(30, ge=5, le=640)):
    """腾讯K线（a-stock-data 工具包，免费不封IP）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="kline-tencent", query={"code": code, "days": days})
    try:
        dim = adapter.tencent_kline(code, count=days)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"腾讯K线获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


# 分时缓存：按「source:code」单槽轮转（面板同屏只看一只股，60s TTL 内
# 切走再切回也够用）；成功（ok/stale）才落缓存，失败不缓存防网络抖动毒化。
_INTRADAY_CACHE: dict = {"time": 0.0, "key": None, "data": None}
_INTRADAY_CACHE_TTL = 60.0


@app.get(f"{API_PREFIX}/intraday")
def api_intraday(code: str, source: str = Query("axdata")):
    """分时数据。默认 source=axdata（通达信协议栈：盘中实时/历史回看）——
    2026-11-06 起腾讯分时接口失效（web.ifzq 反爬挑战 501、proxy.finance
    老路由 404；11-11 已补 /ifzqgtimg 新路由恢复互备），source=tencent
    保留为显式指定/降级腿。

    两腿形状一致：{symbol,date,preclose,points,source,is_today}，points 元素
    [hhmm, price, volume, avg]，volume 单位为手。**请求的腿失败自动换另一腿**
    （成功腿的 source 如实标注在 data.source 里）——单源抽风不再让分时图空白。
    60s 按「source+股票」缓存，成功才落缓存（失败不缓存——网络抖动不许毒化，
    同 mainline 惯例）。未知 source 按 axdata 处理。
    """
    import time as _time

    from .adapters.astock_data import AStockDataAdapter, norm_ticker

    source = "tencent" if source == "tencent" else "axdata"
    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="intraday",
                          query={"code": code, "source": source})
    key = f"{source}:{norm_ticker(code)}"
    now = _time.time()
    if (_INTRADAY_CACHE["data"] is not None
            and _INTRADAY_CACHE["key"] == key
            and now - _INTRADAY_CACHE["time"] < _INTRADAY_CACHE_TTL):
        env.data = _INTRADAY_CACHE["data"]
        return _json(env.to_dict())

    def _fetch(src: str):
        return (adapter.axdata_intraday(code) if src == "axdata"
                else adapter.tencent_intraday(code))

    other = "tencent" if source == "axdata" else "axdata"
    try:
        dim = _fetch(source)
        if dim.status not in (models.OK, models.STALE):
            # 请求腿返回非可用状态（fail() 包装的异常）→ 换腿重试
            dim = _fetch(other)
        env.data = dim.to_dict()
    except Exception as exc:
        try:
            env.data = _fetch(other).to_dict()
        except Exception as exc2:
            env.data = models.DimensionResult(
                status=models.UNAVAILABLE, source="a-stock-data",
                data={"hint": f"分时获取失败（{source}+{other} 均失败）：{exc}; {exc2}"}
            ).to_dict()

    if env.data.get("status") in (models.OK, models.STALE):
        _INTRADAY_CACHE["time"] = now
        _INTRADAY_CACHE["key"] = key
        _INTRADAY_CACHE["data"] = env.data
    else:
        # 适配器 fail() 路径：把 error.message 提到 data.hint 供前端原样转述
        if not (env.data.get("data") or {}).get("hint"):
            msg = (env.data.get("error") or {}).get("message") or "上游无响应"
            env.data["data"] = {
                "hint": msg if "分时" in msg else f"分时获取失败：{msg}"}
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/quote/tencent")
def api_quote_tencent(codes: str = Query(...)):
    """腾讯实时行情（批量，a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))
    code_list = [c.strip() for c in codes.split(",") if c.strip()]

    env = models.Envelope(command="quote-tencent", query={"codes": code_list})
    try:
        dim = adapter.tencent_quote(code_list)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"腾讯行情获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/reports")
def api_reports(code: str = Query(...), pages: int = Query(3, ge=1, le=10)):
    """研报列表（东财，a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="reports", query={"code": code, "pages": pages})
    try:
        dim = adapter.eastmoney_reports(code, max_pages=pages)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"研报获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/eps-forecast")
def api_eps_forecast(code: str = Query(...)):
    """同花顺机构一致预期EPS（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="eps-forecast", query={"code": code})
    try:
        dim = adapter.ths_eps_forecast(code)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"一致预期获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/dragon-tiger/detail")
def api_dragon_tiger_detail(code: str = Query(...), date: str = Query(None)):
    """龙虎榜详情（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="dragon-tiger-detail", query={"code": code, "date": date})
    try:
        dim = adapter.dragon_tiger(code, trade_date=date)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"龙虎榜获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/fund-flow")
def api_fund_flow(code: str = Query(...)):
    """个股资金流向（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="fund-flow", query={"code": code})
    try:
        dim = adapter.fund_flow(code)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"资金流向获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/earnings-forecast")
def api_earnings_forecast(code: str = Query(None), limit: int = Query(50, ge=1, le=500)):
    """业绩预告（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="earnings-forecast", query={"code": code, "limit": limit})
    try:
        dim = adapter.earnings_forecast(code=code, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"业绩预告获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/share-buyback")
def api_share_buyback(code: str = Query(None), limit: int = Query(50, ge=1, le=500)):
    """股票回购（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="share-buyback", query={"code": code, "limit": limit})
    try:
        dim = adapter.share_buyback(code=code, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"股票回购获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/hsgt")
def api_hsgt():
    """沪深股通北向资金实时流向（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="hsgt", query={})
    try:
        dim = adapter.hsgt_realtime()
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"北向资金获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/hot-reason")
def api_hot_reason(date: str = Query(None)):
    """同花顺当日强势股归因（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="hot-reason", query={"date": date})
    try:
        dim = adapter.hot_reason(date=date)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"热股归因获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/institution-survey")
def api_institution_survey(code: str = Query(None), limit: int = Query(50, ge=1, le=500)):
    """机构调研（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="institution-survey", query={"code": code, "limit": limit})
    try:
        dim = adapter.institution_survey(code=code, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"机构调研获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/margin-trading")
def api_margin_trading(code: str = Query(...), limit: int = Query(30, ge=1, le=500)):
    """融资融券明细（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="margin-trading", query={"code": code, "limit": limit})
    try:
        dim = adapter.margin_trading(code, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"融资融券获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/block-trade")
def api_block_trade(code: str = Query(...), limit: int = Query(20, ge=1, le=100)):
    """大宗交易记录（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="block-trade", query={"code": code, "limit": limit})
    try:
        dim = adapter.block_trade(code, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"大宗交易获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/holder-trades")
def api_holder_trades(code: str = Query(None), direction: str = Query(None),
                      limit: int = Query(50, ge=1, le=500)):
    """股东增减持（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="holder-trades", query={
        "code": code, "direction": direction, "limit": limit})
    try:
        dim = adapter.holder_trades(code=code, direction=direction, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"股东增减持获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/holder-num-change")
def api_holder_num_change(code: str = Query(...), limit: int = Query(10, ge=1, le=50)):
    """股东户数变化（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="holder-num-change", query={"code": code, "limit": limit})
    try:
        dim = adapter.holder_num_change(code, limit=limit)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"股东户数变化获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/ticks")
def api_ticks(code: str = Query(...)):
    """逐笔成交明细（a-stock-data 工具包）"""
    from .adapters.astock_data import AStockDataAdapter

    cfg = _get_cfg()
    adapter = AStockDataAdapter(cfg.get("a_stock_data", {}))

    env = models.Envelope(command="ticks", query={"code": code})
    try:
        dim = adapter.tencent_ticks(code)
        env.data = dim.to_dict()
    except Exception as exc:
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="a-stock-data",
            data={"hint": f"逐笔成交获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/board-fund-flow")
def api_board_fund_flow(board_type: str = Query("industry"),
                        period: str = Query("today"), limit: int = Query(20, ge=1, le=100),
                        trade_date: str | None = Query(
                            None, pattern=r"^\d{4}-\d{2}-\d{2}$",
                            description="自由选日期（YYYY-MM-DD）：过去交易日=单日历史，仅主源支持")):
    """板块资金流向排名（主源开盘红/开盘啦、东财备胎仅保今日；
    [data_source] board_fund_flow 可回退）。trade_date 选历史日期时无东财降级。"""
    from .market import board_flow

    env = models.Envelope(command="board-fund-flow", query={
        "board_type": board_type, "period": period, "limit": limit,
        "trade_date": trade_date})
    try:
        payload = board_flow.snapshot(_get_cfg(), board_type, period, limit,
                                      trade_date=trade_date)
        if payload.get("status") == "unavailable":
            env.data = models.DimensionResult(
                status=models.UNAVAILABLE, source=payload.get("source", "kph"),
                data={"hint": payload.get("hint", "板块资金流向不可用")}).to_dict()
        else:
            env.data = models.DimensionResult(
                status=models.OK, source=payload.get("source", "kph"),
                data=payload).to_dict()
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="kph+eastmoney",
            data={"hint": f"板块资金流向获取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/kline")
def api_kline(code: str, days: int = Query(120, ge=20, le=500)):
    """日K + 技术指标（本地计算：config [data_source] primary 决定取数主源，
    默认 a-stock-data 腾讯K线，失败回退 hithink 本地库；指标两路同口径）。

    iFinD 收盘序列兜底已停用（2026-10-08 额度耗尽，用户决策暂时全面停用）。
    """
    from .core.kline import local_kline_dim

    cfg = _get_cfg()
    sym = normalize(code)
    failures: list[str] = []

    try:
        env = models.Envelope(command="kline", query={"input": code})
        env.data = local_kline_dim(cfg, sym, days).to_dict()
        return _json(env.to_dict())
    except Exception as exc:  # noqa: BLE001 — 兜底链要吞掉所有取数/计算异常
        failures.append(f"本地指标计算：{exc}")

    env = models.Envelope(command="kline", query={"input": code})
    env.data = models.DimensionResult(
        status=models.UNAVAILABLE, source="local-kline",
        data={"hint": "本地指标计算失败（iFinD 兜底已停用）",
              "all_sources_failed": failures}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/levels")
def api_levels(code: str, days: int = Query(250, ge=60, le=600)):
    """关键价位（支撑压力/枢轴/前高前低/布林/Keltner/ATR/缺口/斐波/整数关口）。

    与 `/api/kline` 同源：hithink 前复权 + 本地计算。
    """
    from .core.kline import levels_dim

    env = models.Envelope(command="levels", query={"input": code})
    try:
        env.data = levels_dim(_get_cfg(), normalize(code), days).to_dict()
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-levels",
            data={"hint": f"关键价位计算失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/symbol-search")
def api_symbol_search(q: str, limit: int = Query(8, ge=1, le=20)):
    """名称/代码 → 股票候选（前端搜索框用）。"""
    items = _get_adapters()["hithink"].search(q, limit=limit)
    return _json({"ok": True, "command": "symbol-search",
                  "query": {"q": q, "limit": limit},
                  "items": items})


_RT_MAINLINE_CACHE: dict = {"time": 0.0, "data": None}
_TRADING_DAY_CACHE: dict = {"date": None, "ok": False}


def _is_trading_day_today(today: str) -> bool:
    """今天是否交易日：腾讯指数日K最后一根 == today（同 16:00 闸门的判法）。

    防节假日幻影：腾讯实时快照节假日回吐上一交易日数据，不设守卫会造出
    假的"今日"主线行。判不出来时保守返回 False（宁可不显示盘中实时）。
    按日缓存。
    """
    if _TRADING_DAY_CACHE["date"] == today and _TRADING_DAY_CACHE["ok"]:
        return True
    ok = False
    try:
        import urllib.request
        u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
             "?param=sh000001,day,,,5,qfq")
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10).read())
        kl = d["data"]["sh000001"]
        rows = kl.get("qfqday") or kl.get("day") or []
        ok = bool(rows) and str(rows[-1][0]) == today
    except Exception:  # noqa: BLE001 — 取数失败按非交易日返回
        return False   # ⚠️ 失败不落缓存：一次网络抖动不许毒化全天（2026-10-08 实测教训）
    if ok:
        _TRADING_DAY_CACHE["date"] = today   # 只缓存成功判定；真非交易日允许重取
        _TRADING_DAY_CACHE["ok"] = True
    return ok


def _merge_mainline_realtime(cfg, snap: dict) -> dict:
    """把盘中实时主线（今日行，腾讯实时帧重算梯队）并入存储主线历史。

    缓存 60s（realtime_today_frame 自身另有 30s 缓存）。非交易日/盘后：
    today == d_day，不补今日行，仅回传 meta。实时计算失败由调用方降级为存储数据。
    """
    import time as _time
    import polars as pl
    from .market.dashboard import realtime_today_frame
    from .market import mainline as ml
    from .market import service as svc

    now = _time.time()
    if _RT_MAINLINE_CACHE["data"] is not None and now - _RT_MAINLINE_CACHE["time"] < 60:
        cached = _RT_MAINLINE_CACHE["data"]
    else:
        frame, meta = realtime_today_frame(cfg)
        today, d_day = meta["today"], meta["d_day"]
        live = today != d_day and _is_trading_day_today(today)
        rows_today: dict[str, list] = {"concept": [], "industry": []}
        if live:
            limit_rows = (frame.filter(
                (pl.col("date") == pl.lit(today).str.to_date("%Y-%m-%d"))
                & (pl.col("consecutive_limit_ups") >= 1))
                .select(["date", "symbol", "consecutive_limit_ups", "amount"]))
            for kind in rows_today:
                map_df, _ = svc.concepts_map(cfg, kind)
                rows = ml.mainline_rows(limit_rows, map_df, kind)
                if not rows.is_empty():
                    rows_today[kind] = [
                        {**r, "date": str(r.get("date"))[:10]}
                        for r in rows.to_dicts()]
        meta = {**meta, "intraday": live}
        cached = {"rows_today": rows_today, "meta": meta}
        _RT_MAINLINE_CACHE["time"] = now
        _RT_MAINLINE_CACHE["data"] = cached

    meta = cached["meta"]
    today = meta["today"]
    kinds = {}
    live = bool(meta.get("intraday"))
    for kind in ("concept", "industry"):
        stored_rows = (snap.get("kinds", {}).get(kind) or [])
        if live:
            # 盘中：剔存储中的今日行（防重）后追加实时今日行
            kept = [r for r in stored_rows if str(r.get("date"))[:10] != today]
            kinds[kind] = kept + cached["rows_today"][kind]
        else:
            # 盘后/非交易日：本地库已是今日（或无需今日行），存储原样保留——
            # 无条件剔今日再补空会把已落库的当日主线删没（2026-10-08 盘后只显示 09-30 的教训）
            kinds[kind] = stored_rows
    out = dict(snap)
    out["kinds"] = kinds
    out["realtime"] = {"today": today, "rt_time": meta.get("rt_time"),
                       "coverage": meta.get("coverage"),
                       "intraday": live}
    return out


@app.get(f"{API_PREFIX}/nt-monitor")
def api_nt_monitor(days: int = Query(10, ge=1, le=60)):
    """国家队宽基 ETF 份额「当日增持」监控（周频按交易日均摊估计）。

    只输出交易日；数据到最新完整披露周。非交易日/数据缺失时如实返回
    unavailable（前端显示灰字，不编造）。
    """
    from .nt_monitor import monitor_summary
    env = models.Envelope(command="nt-monitor", query={"days": days})
    env.data = monitor_summary(days=days)
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/mainline")
def api_mainline(days: int = Query(30, ge=5, le=120), refresh: bool = False,
                 realtime: bool = Query(False)):
    """每日主线排行（概念 + 行业，基于涨停梯队）。本地计算。

    `refresh=true` 会重算目标区间（含全市场成分构建，首次较慢）。
    `realtime=true` 盘中并入今日实时梯队行（60s 缓存），非交易日自动忽略。
    """
    from .market import service as market_service

    env = models.Envelope(command="mainline", query={"days": days, "refresh": refresh,
                                                     "realtime": realtime})
    try:
        env.data = market_service.mainline_snapshot(
            _get_cfg(), days=days, refresh=refresh)
        if realtime:
            try:
                env.data = _merge_mainline_realtime(_get_cfg(), env.data)
            except Exception:  # noqa: BLE001 — 实时失败降级为存储数据
                import logging
                logging.getLogger(__name__).warning(
                    "mainline realtime merge failed", exc_info=True)
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-mainline",
            data={"hint": f"主线计算失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/rps")
def api_rps(days: int = Query(12, ge=7, le=30), refresh: bool = False):
    """板块涨幅轮动矩阵（概念 + 行业，每日按平均涨幅排序）。本地计算。"""
    from .market import service as market_service

    env = models.Envelope(command="rps", query={"days": days, "refresh": refresh})
    try:
        env.data = market_service.rps_snapshot(
            _get_cfg(), days=days, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-rps",
            data={"hint": f"板块轮动计算失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/quote")
def api_quote(codes: str):
    """实时行情快照（主源 a-stock-data 体系腾讯快照，hithink 同花顺回退）。

    codes: 逗号分隔的 thscode（建议 ≤50 只）。返回每股实时价 + market_session
    （pre/open/post/closed）+ is_trading，前端据 is_trading 决定是否定时轮询。
    """
    from .adapters.hithink import HithinkAdapter

    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return _json({"schema_version": models.SCHEMA_VERSION,
                      "command": "quote",
                      "generated_at": models.now_iso(),
                      "data": {"item": [], "is_trading": False}})

    # 主源 a-stock-data 体系（腾讯免费快照，不封 IP）；失败回退 hithink 同花顺实时
    from .adapters.tencent import TencentAdapter

    snap = TencentAdapter(_get_cfg().get("tencent", {})).snapshot_chunked(code_list)
    source_used = "tencent-free"
    # STALE（非交易日的最近收盘）是合法结果——"陈旧只是标注，不是错误"，
    # 否则周末/节假日自选股与看板报价会整体变 --
    _usable = (models.OK, models.STALE)
    if snap.status not in _usable or not (snap.data or {}).get("item"):
        ht = HithinkAdapter(_get_cfg().get("hithink", {}))
        hsnap = ht.realtime_snapshot(code_list)
        if hsnap.status in _usable and (hsnap.data or {}).get("item"):
            snap = hsnap
            source_used = "hithink"

    # 两个源字段名不同，统一成前端约定的 schema。
    # hithink 实时快照不含 name，用名称映射补齐（失败则留空，不阻断）。
    name_map: dict[str, str] = {}
    if source_used == "hithink":
        try:
            from .store.market_data import symbol_names
            name_map = {
                r["symbol"]: r["name"]
                for r in symbol_names(_get_cfg()).to_dicts()
            }
        except Exception as exc:
            name_map = {}
    raw_items = (snap.data or {}).get("item", []) if snap.status in _usable else []
    norm_items = [_normalize_quote_item(it, source_used, name_map) for it in raw_items]

    session, is_session = market_session()

    payload = dict(snap.data) if snap.status in _usable else {}
    payload["item"] = norm_items
    payload["total"] = len(norm_items)
    payload["market_session"] = session
    payload["is_trading"] = bool(is_session)
    payload["source_used"] = source_used
    payload["server_time"] = models.now_iso()
    payload["as_of"] = snap.meta.as_of
    payload["stale"] = bool((snap.meta.freshness or {}).get("stale"))
    return _json({"schema_version": models.SCHEMA_VERSION,
                  "command": "quote",
                  "generated_at": models.now_iso(),
                  "data": payload,
                  **({"error": snap.error} if snap.status not in _usable else {})})


def _normalize_quote_item(it: dict, source: str,
                          name_map: dict[str, str] | None = None) -> dict:
    """把 hithink / tencent 的原始 item 归一化成统一字段。

    统一输出：symbol/name/last_price/price_change_ratio_pct/open/high/low/
    prev_close/volume/turnover，并保留各源来源标记。
    """
    if source == "tencent-free":
        return {
            "symbol": it.get("symbol"),
            "name": it.get("name"),
            "last_price": it.get("price"),
            "price_change": it.get("change"),
            "price_change_ratio_pct": it.get("change_pct"),
            "open_price": it.get("open"),
            "high_price": it.get("high"),
            "low_price": it.get("low"),
            "prev_price": it.get("prev_close"),
            "volume": it.get("volume"),
            "turnover": it.get("amount"),
            "_source": "tencent",
        }
    # hithink：字段已接近，补 symbol/name（实时快照不返回 name，用名称映射）
    symbol = it.get("thscode")
    return {
        "symbol": symbol,
        "name": it.get("name") or (name_map or {}).get(symbol),
        "last_price": it.get("last_price"),
        "price_change": it.get("price_change"),
        "price_change_ratio_pct": it.get("price_change_ratio_pct"),
        "open_price": it.get("open_price"),
        "high_price": it.get("high_price"),
        "low_price": it.get("low_price"),
        "prev_price": it.get("prev_price"),
        "volume": it.get("volume"),
        "turnover": it.get("turnover"),
        "_source": "hithink",
    }


# ============================================
# 网页端凭证：数据源 API Key + 大模型接口
# ====================================

@app.get(f"{API_PREFIX}/credentials")
def api_credentials_get(request: Request):
    """返回脱敏后的凭证状态（不回读明文 key）——按当前用户隔离。"""
    from . import credentials
    return _json(credentials.public_view(user=request.state.user))


@app.post(f"{API_PREFIX}/credentials/source")
def api_credentials_set_source(request: Request, body: dict):
    """保存/更新一个数据源凭证（写入当前用户自己的凭证文件）。
    body: {id, api_key?, api_base_url?}；api_key 省略=不改，传 ''=清除。
    """
    from . import credentials
    u = request.state.user
    sid = (body.get("id") or "").strip()
    if not sid:
        return JSONResponse({"error": "缺少数据源 id"}, status_code=422)
    api_key = body.get("api_key")
    base_url = body.get("api_base_url")
    credentials.set_source(
        sid,
        api_key=(api_key if api_key is not None else None),
        base_url=(base_url if base_url is not None else None),
        user=u)
    return _json({"ok": True, "id": sid,
                  "state": credentials.public_view(user=u)["sources"].get(sid)})


@app.delete(f"{API_PREFIX}/credentials/source")
def api_credentials_del_source(request: Request, id: str):
    from . import credentials
    credentials.remove_source(id, user=request.state.user)
    return _json({"ok": True, "id": id})


@app.post(f"{API_PREFIX}/credentials/llm")
def api_credentials_set_llm(request: Request, body: dict):
    """保存/更新一套大模型接口（写入当前用户自己的凭证文件）。
    body: {id, kind?, base_url, model, api_key?}；api_key 省略=不改。
    kind=claude-cli（订阅通道）允许 base_url/model 为空；仅管理员可用——
    订阅走机主本机命令行，访客不得占用。
    """
    from . import credentials
    u = request.state.user
    pid = (body.get("id") or "default").strip()
    kind = (body.get("kind") or "api").strip()
    if kind not in ("api", "claude-cli"):
        return JSONResponse({"error": "kind 只能是 api/claude-cli"}, status_code=422)
    if kind == "claude-cli" and not u.get("is_admin"):
        return JSONResponse({"error": "订阅通道（Claude 命令行）仅管理员可用，请填写自己的 API Key"},
                            status_code=400)
    base_url = (body.get("base_url") or "").strip()
    model = (body.get("model") or "").strip()
    if kind == "api" and (not base_url or not model):
        return JSONResponse({"error": "base_url 与 model 必填"}, status_code=422)
    api_key = body.get("api_key")
    max_tokens = body.get("max_output_tokens")
    context_window = body.get("context_window")
    credentials.upsert_llm_profile(
        profile_id=pid, base_url=base_url, model=model,
        api_key=(api_key if api_key is not None else None),
        max_output_tokens=(max_tokens if isinstance(max_tokens, int) else None),
        context_window=(context_window if isinstance(context_window, int) else None),
        kind=kind, user=u)
    if body.get("make_default"):
        credentials.set_default_llm_profile(pid, user=u)
    return _json({"ok": True, "id": pid,
                  "state": credentials.public_view(user=u)["llm"]})


@app.post(f"{API_PREFIX}/credentials/llm/default")
def api_credentials_default_llm(request: Request, body: dict):
    """把某套已保存的大模型接口设为默认（保存后各模块即时跟随，无需重启）。"""
    from . import credentials
    u = request.state.user
    pid = (body.get("id") or "").strip()
    try:
        credentials.set_default_llm_profile(pid, user=u)
    except credentials.CredentialError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return _json({"ok": True, "id": pid,
                  "state": credentials.public_view(user=u)["llm"]})


@app.delete(f"{API_PREFIX}/credentials/llm")
def api_credentials_del_llm(request: Request, id: str):
    from . import credentials
    credentials.remove_llm_profile(id, user=request.state.user)
    return _json({"ok": True, "id": id})


@app.post(f"{API_PREFIX}/credentials/test")
def api_credentials_test(request: Request, body: dict):
    """连通性测试（按当前用户凭证）。
    - {kind:'source', id, api_key?}：数据源最小实拉（tdxquant 已接探测，其余占位）
    - {kind:'llm', id?, base_url?, model?, api_key?}：发一次极简对话
    """
    u = request.state.user
    kind = body.get("kind")
    if kind == "source":
        sid = (body.get("id") or "").strip()
        if sid == "tdxquant":
            # 优先用请求里的候选 Key（表单草稿），否则用已保存凭证
            from . import credentials
            from .adapters.tdxquant import probe as tdx_probe
            key = (body.get("api_key") or "").strip() or (
                (credentials.get_source("tdxquant", user=u) or {}).get("api_key") or "")
            ok, msg = tdx_probe(key)
            return _json({"ok": ok, "message": msg})
        if sid in ("tushare", "tickflow"):
            # 2026-11-07：Tushare Pro / TickFlow 在线 API（docs.tickflow.org）
            from . import credentials
            from .adapters.tickflow_cloud import probe as tf_probe
            from .adapters.tushare import probe as ts_probe
            saved = credentials.get_source(sid, user=u) or {}
            key = (body.get("api_key") or "").strip() or (saved.get("api_key") or "")
            base = (body.get("api_base_url") or "").strip() or saved.get("api_base_url")
            probe = ts_probe if sid == "tushare" else tf_probe
            ok, msg = probe(key, base)
            return _json({"ok": ok, "message": msg})
        return _json({"ok": False, "error": "该数据源暂不支持连接测试"})
    if kind == "llm":
        # 优先用已保存的 profile，否则用请求里的临时值
        from . import credentials
        p = None
        if body.get("id"):
            p = credentials.get_llm_profile(body["id"], user=u)
        channel_kind = (p or {}).get("kind") or body.get("llm_kind") or "api"
        if channel_kind == "claude-cli" and not u.get("is_admin"):
            return _json({"ok": False, "error": "订阅通道仅管理员可用，请填写自己的 API Key"})
        if channel_kind == "claude-cli":
            # 订阅通道：探测命令行 + 一句极简对话，三态返回
            from .llm_client import find_claude, call_cli
            if not find_claude():
                return _json({"ok": False, "code": "cli_not_installed",
                              "error": "未检测到 Claude Code 命令行（订阅通道需本机安装并 claude login）"})
            try:
                call_cli([{"role": "user", "content": "只回复两个字：正常"}],
                         timeout=30)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                code = "cli_not_logged_in" if "未登录" in msg else "cli_failed"
                return _json({"ok": False, "code": code, "error": msg})
            return _json({"ok": True, "via": "claude-cli"})
        import httpx
        base_url = (p or {}).get("base_url") or body.get("base_url")
        model = (p or {}).get("model") or body.get("model")
        api_key = (p or {}).get("api_key") or body.get("api_key")
        if not base_url or not model:
            return JSONResponse({"ok": False, "error": "缺少 base_url/model"},
                                status_code=422)
        try:
            r = httpx.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {api_key or 'sk-none'}"},
                json={"model": model,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 4},
                      timeout=20)
        except Exception as e:  # noqa: BLE001
            return _json({"ok": False, "error": f"请求失败：{e}"})
        if r.status_code == 200:
            return _json({"ok": True, "model": model})
        return _json({"ok": False, "http_status": r.status_code,
                      "error": r.text[:200]})
    # source 测试占位（待各付费源适配器落地）
    return _json({"ok": False, "error": "source 连接测试待对应适配器接入后提供"})


@app.get(f"{API_PREFIX}/global/quotes")
def api_global_quotes(request: Request, symbols: str = Query(...)):
    """美股/港股（及其它市场）实时快照，走当前用户自己的 TickFlow 凭证。

    symbols 逗号分隔完整代码（AAPL.US、00700.HK、600519.SH），最多 20 个。
    """
    from . import credentials
    from .adapters import tickflow_cloud as tfc
    from .adapters import global_index as gidx

    u = request.state.user
    saved = credentials.get_source("tickflow", user=u) or {}
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:20]
    if not syms:
        return _json({"ok": False, "error": "请传入标的代码（如 AAPL.US、00700.HK）"})
    # 指数走腾讯免费源（TickFlow 无指数），其余走 TickFlow（用户拍板 2026-11-07）
    idx_syms = [s for s in syms if s in gidx.INDEX_MAP]
    tf_syms = [s for s in syms if s not in gidx.INDEX_MAP]
    items: list[dict] = []
    if idx_syms:
        try:
            items.extend(gidx.quotes(idx_syms))
        except gidx.GlobalIndexError as e:
            return _json({"ok": False, "error": str(e)})
    if tf_syms:
        try:
            items.extend(tfc.quotes(saved.get("api_key") or "", tf_syms,
                                    saved.get("api_base_url")))
        except tfc.TickflowError as e:
            return _json({"ok": False, "error": str(e)})
    return _json({"ok": True, "items": items})


@app.get(f"{API_PREFIX}/global/kline")
def api_global_kline(request: Request, symbol: str = Query(...),
                     days: int = Query(120, ge=5, le=500)):
    """美股/港股日K（TickFlow /v1/klines period=1d），行式 OHLCV。"""
    from . import credentials
    from .adapters import tickflow_cloud as tfc
    from .adapters import global_index as gidx

    u = request.state.user
    saved = credentials.get_source("tickflow", user=u) or {}
    sym = symbol.strip().upper()
    try:
        if sym in gidx.INDEX_MAP:
            rows = gidx.kline(sym, count=days)
        else:
            rows = tfc.kline(saved.get("api_key") or "", sym,
                             period="1d", count=days, base_url=saved.get("api_base_url"))
    except (tfc.TickflowError, gidx.GlobalIndexError) as e:
        return _json({"ok": False, "error": str(e)})
    return _json({"ok": True, "symbol": sym, "rows": rows})


@app.get(f"{API_PREFIX}/global/intraday")
def api_global_intraday(symbol: str = Query(...)):
    """指数当日分时（腾讯 minute/query）。A 股/港股指数全量，美股指数上游只有
    当前点→points 空，前端如实提示。个股分时请用 /api/intraday。"""
    from .adapters import global_index as gidx

    sym = symbol.strip().upper()
    if sym not in gidx.INDEX_MAP:
        return _json({"ok": False, "error": f"分时仅支持指数（{sym} 不在指数目录）"})
    try:
        doc = gidx.intraday(sym)
    except gidx.GlobalIndexError as e:
        return _json({"ok": False, "error": str(e)})
    return _json({"ok": True, "symbol": sym, **doc})


@app.get(f"{API_PREFIX}/strategies")
def api_strategies():
    """内置选股策略目录（id/名称/描述/参数）。"""
    from .market import service as market_service

    return _json({"schema_version": models.SCHEMA_VERSION,
                  "command": "strategies",
                  "generated_at": models.now_iso(),
                  "strategies": market_service.list_available_strategies()})


@app.get(f"{API_PREFIX}/strategy/ai-status")
def api_strategy_ai_status():
    """检查 AI 策略生成是否已配置。"""
    # 目前不支持 AI 策略生成，返回 configured: false
    return _json({"configured": False})


@app.get(f"{API_PREFIX}/screen")
def api_screen(strategy: str, limit: int | None = Query(None, ge=1, le=500),
               realtime: str = Query("auto")):
    """运行某个内置选股策略，返回 as_of 日的入选名单与打分。

    realtime：auto=盘中走实时帧（今日），盘后走落库；true/false 强制。
    注意：需要 EMA/MA 等复杂指标的策略（如 MA 金叉）在实时帧中无法计算，
    会自动降级到使用本地库（昨日完整数据）。
    """
    from .market import service as market_service
    from .market.sessions import market_session
    from .market.strategies import get_strategy

    cfg = _get_cfg()
    _, is_trading = market_session()
    rt = None if realtime == "auto" else realtime.lower() in ("1", "true", "yes")
    use_rt = rt if rt is not None else is_trading

    # 检查策略是否需要复杂指标（EMA/MA 等），实时帧可能无法计算
    strategy_def = get_strategy(strategy)
    needs_complex_indicators = any(
        'ema' in str(getattr(strategy_def, 'entry', '')).lower() or
        'ma' in str(getattr(strategy_def, 'entry', '')).lower()
        for _ in [1]
    )

    env = models.Envelope(command="screen",
                          query={"strategy": strategy, "limit": limit,
                                 "realtime": realtime})
    try:
        # 需要复杂指标的策略，强制使用本地库（昨日完整数据）
        if use_rt and needs_complex_indicators:
            env.data = market_service.screen_strategy(
                cfg, strategy, limit=limit)
        elif use_rt:
            env.data = market_service.screen_strategy_realtime(
                cfg, strategy, limit=limit)
        else:
            env.data = market_service.screen_strategy(
                cfg, strategy, limit=limit)
    except Exception as exc:  # noqa: BLE001
        env.data = {"strategy_id": strategy, "total": 0, "rows": [],
                    "unavailable": f"选股失败：{exc}"}
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/regime")
def api_regime(days: int = Query(60, ge=10, le=250), refresh: bool = False):
    """市场环境（情绪周期）：情绪分 + 5 档状态 + 6 阶段 + 近 N 日时序。

    **本地计算**（hithink 本地全市场日线 → 指标 → 聚合 → 规则分类），不再依赖
    tickflow 服务。`refresh=true` 会重算目标区间（全市场，约几秒）。
    """
    from .market import service as regime_service

    env = models.Envelope(command="regime", query={"days": days, "refresh": refresh})
    try:
        env.data = regime_service.snapshot(_get_cfg(), days=days, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+local-regime",
            data={"hint": f"情绪周期计算失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/phase-mainline-matrix")
def api_phase_mainline_matrix(days: int = Query(120, ge=20, le=250),
                              kind: str = Query("concept", pattern="^(concept|industry)$")):
    """情绪阶段 × 主线历史矩阵（阶段段内 top5 主线）。"""
    from .market.phase_mainline import phase_mainline_matrix
    try:
        return _json(phase_mainline_matrix(_get_cfg(), days=days, kind=kind))
    except Exception as exc:  # noqa: BLE001
        return _json({"available": False, "reason": "unavailable",
                      "error": str(exc), "segments": []})


@app.get(f"{API_PREFIX}/dashboard")
def api_dashboard(date: str | None = Query(None),
                  realtime: str = Query("auto"),
                  skip_turnover: bool = Query(False)):
    """实时看板：指数 / 宽度 / 涨跌停 / 趋势 / 情绪雷达 / 概念行业热度 / 榜单 / 涨停梯队。

    三源：hithink（全市场帧）+ 本地指数日线（腾讯兜底）+ tencent（最新日换手率）；不依赖 tickflow。
    realtime：auto / true / false。
    skip_turnover：跳过换手率加载（首次加载优化）。
    """
    from .market import dashboard as dash

    rt = None if realtime == "auto" else realtime.lower() in ("1", "true", "yes")
    env = models.Envelope(command="dashboard",
                          query={"date": date, "realtime": realtime})
    try:
        env.data = dash.build_dashboard(_get_cfg(), date, realtime=rt,
                                        skip_turnover=skip_turnover)
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink+tencent",
            data={"hint": f"看板构建失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/dashboard-layout")
def api_dashboard_layout_get():
    """看板区块布局（显隐+排序）：全员可读；缺省=默认顺序。"""
    from . import dashboard_layout as dl
    path = Path(__file__).resolve().parents[1] / "data" / "dashboard_layout.json"
    return _json(dl.load(path))


@app.post(f"{API_PREFIX}/dashboard-layout")
def api_dashboard_layout_save(request: Request, body: dict):
    """保存看板布局（仅管理员；非法 id/形状 400 不 500）。"""
    from . import dashboard_layout as dl
    path = Path(__file__).resolve().parents[1] / "data" / "dashboard_layout.json"
    try:
        doc = dl.save(path, body)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    return _json({"ok": True, **doc})


@app.get(f"{API_PREFIX}/dashboard/amount-history")
def api_dashboard_amount_history(days: int = Query(60, ge=5, le=250)):
    """看板成交额 KPI 点击弹窗：近 N 个交易日全市场成交额日序列（本地仓库，A 股口径）。"""
    from .market import dashboard as dash

    env = models.Envelope(command="amount_history", query={"days": days})
    try:
        env.data = {"days": days, "series": dash.amount_history(_get_cfg(), days)}
    except Exception as exc:  # noqa: BLE001
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="local-parquet",
            data={"hint": f"历史成交额读取失败：{exc}"}).to_dict()
    return _json(env.to_dict())


@app.get(f"{API_PREFIX}/dashboard/reconcile")
def api_dashboard_reconcile():
    """最近一次收盘核对结果（data/reconcile_latest.json）；未跑则 available=false。"""
    import json

    path = Path(__file__).resolve().parents[1] / "data" / "reconcile_latest.json"
    if not path.exists():
        return _json({"available": False})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _json({"available": False, "error": str(exc)})
    data["available"] = True
    return _json(data)


@app.get(f"{API_PREFIX}/dashboard/members")
def api_dashboard_members(kind: str = Query(..., pattern="^(concept|industry)$"),
                          name: str = Query(...),
                          realtime: bool = Query(False)):
    """板块成分股（读 hithink membership，附名称 + 当日涨幅）。

    `realtime=true` 盘中改用腾讯实时帧的今日涨幅（30s 缓存，与主线实时帧共享，
    同 _is_trading_day_today 守卫防节假日回吐）；盘后/非交易日/实时失败
    自动回落到存储的最近交易日。
    """
    from .market import dashboard
    from .store import concepts as concepts_store
    from .store import market_data as store_md

    cfg = _get_cfg()
    map_df, _ = concepts_store.concept_map_df(cfg, kind)
    syms = (map_df.filter(pl.col(kind) == name)["_sym_up"]).to_list()
    name_of = {r["symbol"]: r["name"]
               for r in store_md.symbol_names(cfg).to_dicts()}

    end = store_md.db_max_date(cfg)
    as_of, intraday, rt_time = end, False, None
    change_of: dict = {}
    if realtime:
        today = date.today().isoformat()
        if today != end and _is_trading_day_today(today):
            try:
                from .market.dashboard import realtime_today_frame
                frame, meta = realtime_today_frame(cfg)
                dayrows = frame.filter(
                    pl.col("date") == pl.lit(today).str.to_date("%Y-%m-%d"))
                change_of = {
                    r["symbol"]: r["change_pct"]
                    for r in dayrows.select("symbol", "change_pct")
                        .iter_rows(named=True)}
                if change_of:
                    as_of, intraday = today, True
                    rt_time = meta.get("rt_time")
            except Exception:  # noqa: BLE001 — 实时失败降级为存储口径
                change_of = {}
    if not change_of:
        # 当日涨幅：读已缓存的全市场底表（最近交易日），不重算
        dayf = dashboard._base_frame(cfg, end).filter(
            pl.col("date") == date.fromisoformat(end))
        change_of = {
            r["symbol"]: r["change_pct"]
            for r in dayf.select("symbol", "change_pct").iter_rows(named=True)}

    rows = [
        {"symbol": s, "name": name_of.get(s), "change_pct": change_of.get(s)}
        for s in syms
    ]
    rows.sort(key=lambda r: r["change_pct"] if r["change_pct"] is not None else -999,
              reverse=True)
    return _json({"kind": kind, "name": name, "as_of": as_of,
                  "intraday": intraday, "rt_time": rt_time,
                  "total": len(rows), "rows": rows})


@app.get(f"{API_PREFIX}/sector-intraday")
def api_sector_intraday(kind: str = Query(..., pattern="^(concept|industry)$"),
                        name: str = Query(...),
                        day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """板块等权分时 vs 全市场（盘后分钟库，60s 缓存，非实时）。"""
    from .market.sector_intraday import sector_intraday
    try:
        return _json(sector_intraday(_get_cfg(), kind, name, day))
    except Exception as exc:  # noqa: BLE001 — 辅助图不拖垮成分股弹窗
        return _json({"available": False, "reason": "unavailable",
                      "error": str(exc), "points": []})


@app.get(f"{API_PREFIX}/sector-leader-scores")
def api_sector_leader_scores(kind: str = Query(..., pattern="^(concept|industry)$"),
                             name: str = Query(...)):
    """板块龙头六因子评分 + 热度/风险拆解（最近收盘 + 腾讯市值换手）。"""
    from .market.leader_score import sector_leader_scores
    try:
        return _json(sector_leader_scores(_get_cfg(), kind, name))
    except Exception as exc:  # noqa: BLE001
        return _json({"available": False, "reason": "unavailable",
                      "error": str(exc), "rows": []})


@app.get(f"{API_PREFIX}/nt-position")
def api_nt_position(code: str):
    """国家队在本股上的持仓结论（季报粒度）。"""
    dim = _get_adapters()["national_team"].position(normalize(code).ts_code)
    return _json(models.Envelope(command="nt-position",
                                 query={"input": code}, data=dim.to_dict()).to_dict())


@app.get(f"{API_PREFIX}/nt-etf")
def api_nt_etf(weeks: int = Query(12, ge=4, le=60)):
    """汇金宽基 ETF 份额序列（周频温度计）。"""
    dim = _get_adapters()["national_team"].etf_shares(weeks)
    return _json(models.Envelope(command="nt-etf", data=dim.to_dict()).to_dict())


@app.get(f"{API_PREFIX}/valuation")
def api_valuation(code: str):
    """估值快照（PE/PB/PS，hithink-finance 透传）。"""
    sym = normalize(code)
    adapter = _get_adapters()["hithink"]
    try:
        envelope, cmd = adapter._run(
            ["valuation", "snapshot", "--thscodes", sym.ts_code])
        data = envelope.get("data") or {}
        return _json(models.Envelope(command="valuation",
                                     query={"input": code}, data=data).to_dict())
    except Exception as exc:  # noqa: BLE001 — 未配置数据源不能变成 500
        env = models.Envelope(command="valuation", query={"input": code})
        env.data = models.DimensionResult(
            status=models.UNAVAILABLE, source="hithink-finance",
            data={"hint": f"估值数据源暂不可用：{exc}"}).to_dict()
        return _json(env.to_dict())


@app.get(f"{API_PREFIX}/indexes")
def api_indexes():
    """六大指数行情快照（市场总览页）。hithink 索引快照（iFinD 已停用——额度耗尽，
    2026-10-08 用户决策暂时全面停用）。
    返回结构与前端契约一致：item[].{thscode,last_price,price_change_ratio_pct,...}。"""
    INDEXES = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH",
               "000300.SH", "000905.SH"]
    adapter = _get_adapters()["hithink"]
    envelope, cmd = adapter._run(
        ["index", "snapshot", "--thscodes", ",".join(INDEXES)])
    data = envelope.get("data") or {}
    return _json(models.Envelope(command="indexes",
                                 query={"source": "hithink"}, data=data).to_dict())


@app.get(f"{API_PREFIX}/quotes")
def api_quotes(code: str):
    """实时行情快照（个股分析页的统一行情口径）。

    主源 **a-stock-data（腾讯快照，免费不封 IP）**：价格/涨跌/开高低/换手/估值/市值。
    iFinD 已停用（2026-10-08 额度耗尽，用户决策暂时全面停用）——交叉验证与回退均已移除。
    """
    from .adapters.astock_data import AStockDataAdapter

    sym = normalize(code)
    cfg = _get_cfg()
    env = models.Envelope(command="quotes", query={"input": code})

    asd = AStockDataAdapter(cfg.get("a_stock_data", {}))
    tx = asd.tencent_quote([sym.bare])
    if tx.status == models.OK and (tx.data or {}).get("item"):
        item = _normalize_astockdata_quote(tx.data["item"][0], sym)
        env.data = models.DimensionResult(
            status=models.OK, source="a-stock-data/tencent",
            meta=tx.meta,
            data={"item": [item], "total": 1},
        ).to_dict()
        return _json(env.to_dict())

    # 主源失败（iFinD 回退已停用，无第二行情源）
    env.data = models.DimensionResult(
        status=models.UNAVAILABLE, source="a-stock-data/tencent",
        data={"hint": "腾讯快照获取失败（iFinD 已停用，无回退源），请稍后重试"}).to_dict()
    return _json(env.to_dict())


def _normalize_astockdata_quote(tq: dict, sym) -> dict:
    """腾讯快照（a-stock-data）归一化成与 iFinD 快照一致的字段名（前端契约）。

    腾讯 volume 单位是手 → 乘 100 换算成股，与 hithink/iFinD 口径一致。
    """
    vol = tq.get("volume")
    return {
        "thscode": sym.ts_code,
        "ticker": sym.bare,
        "name": tq.get("name"),
        "last_price": tq.get("price"),
        "price_change": tq.get("change"),
        "price_change_ratio_pct": tq.get("change_pct"),
        "open_price": tq.get("open"),
        "high_price": tq.get("high"),
        "low_price": tq.get("low"),
        "prev_price": tq.get("prev_close"),
        "volume": vol * 100 if vol is not None else None,
        "turnover": tq.get("amount"),
        "turnover_ratio": tq.get("turnover_ratio"),
        "pe_ttm": tq.get("pe_ttm"),
        "pb": tq.get("pb"),
        "market_cap": tq.get("market_cap"),
        "time": tq.get("time"),
        "_source": "a-stock-data/tencent",
    }






@app.get(f"{API_PREFIX}/zsxq-latest")
def api_zsxq_latest():
    """最新一份调研鹅纪要（元信息 + 开头摘录）。"""
    dim = _get_adapters()["zsxq"].latest()
    return _json(models.Envelope(command="zsxq-latest", data=dim.to_dict()).to_dict())


@app.get(f"{API_PREFIX}/zsxq-search")
def api_zsxq_search(keyword: str, days: int = Query(7, ge=1, le=30)):
    """近期纪要关键词搜索。"""
    dim = _get_adapters()["zsxq"].search(keyword, days=days)
    return _json(models.Envelope(command="zsxq-search",
                                 query={"keyword": keyword}, data=dim.to_dict()).to_dict())


# ---------------------------------------------------------------- 请求用户参数

def _request_user_kwargs(request: Request) -> dict:
    if _auth._TEST_BYPASS:
        return {}
    u = request.state.user
    return {"user": u["username"], "is_admin": bool(u.get("is_admin", False))}


@app.get(f"{API_PREFIX}/news")
def api_news(industry: str | None = None, limit: int = 50):
    """investment-news 各赛道最新动向。

    limit: 每个赛道返回条数（默认 50，供产业资讯整页使用；原卡片用小 limit）。
    """
    dim = _get_adapters()["investment_news"].latest(
        industry=industry, max_items=max(1, min(limit, 200)))
    return _json(models.Envelope(command="news", data=dim.to_dict()).to_dict())


@app.get(f"{API_PREFIX}/news-search")
def api_news_search(keyword: str, days: int | None = Query(None, ge=1, le=30)):
    """investment-news 全球产业新闻关键词搜索。"""
    dim = _get_adapters()["investment_news"].search(keyword, days=days)
    return _json(models.Envelope(command="news-search",
                                 query={"keyword": keyword}, data=dim.to_dict()).to_dict())


@app.get(f"{API_PREFIX}/market/limit-ladder")
def api_market_limit_ladder(day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """连板梯队逐层概念/行业分布（本地涨停池收盘口径）。"""
    from .market.limit_ladder import limit_ladder
    try:
        return _json(limit_ladder(_get_cfg(), day))
    except Exception as exc:  # noqa: BLE001
        return _json({"available": False, "reason": "unavailable",
                      "error": str(exc), "layers": []})


@app.get(f"{API_PREFIX}/market/limit-pools")
def api_market_limit_pools():
    """涨跌停数据元：涨停/跌停池与家数（hithink；iFinD 已停用——额度耗尽，
    2026-10-08 用户决策暂时全面停用）。

    返回 data: {"as_of", "sources": {up,down}, "up": {count,rows:[{symbol,name}]},
    "down": {...}, "note"}。
    """
    ht_adapters = _get_adapters()
    hu = ht_adapters["hithink"].limit_pool("up", 200)
    hd = ht_adapters["hithink"].limit_pool("down", 200)
    conv = lambda dim: {"count": (dim.data or {}).get("total") or len((dim.data or {}).get("item") or []),
                        "rows": [{"symbol": it.get("thscode") or "", "name": it.get("name") or ""}
                                 for it in (dim.data or {}).get("item") or []]}
    up_data, down_data = conv(hu), conv(hd)
    as_of = hu.meta.as_of if hu.status == models.OK else None

    # 实时两路都拿不到涨停名单（盘前/非交易日，实时端点只查"今天"、无法回看历史）：
    # 用本地情绪周期里**最近一个已收盘交易日**的计数补齐，避免面板显示"涨停 0 只"的
    # 错误口径。实时名单（rows）仍为空，由前端标注"展示最近交易日统计"。
    realtime = up_data["count"] > 0 or down_data["count"] > 0
    latest_trade_date = None
    backfill_breadth = None
    if not realtime:
        from .market import service as market_service
        try:
            local = market_service.snapshot(_get_cfg(), days=5)["latest"]
        except Exception:  # noqa: BLE001
            local = None
        if local:
            latest_trade_date = local["date"]
            as_of = latest_trade_date
            up_data["count"] = int(local["limit_up"] or 0)
            down_data["count"] = int(local["limit_down"] or 0)
            backfill_breadth = {
                "scope": f"全市场（最近交易日 {latest_trade_date}，本地计算）",
                "up": local.get("up_count"), "down": local.get("down_count"),
                "limit_up": local.get("limit_up"),
                "limit_down": local.get("limit_down"),
                "amount": local.get("total_amount"),
            }

    env = models.Envelope(command="limit-pools", query={"source": "hithink"})
    env.data = {
        "as_of": as_of,
        "realtime": realtime,
        "latest_trade_date": latest_trade_date,
        "sources": {"up": "hithink", "down": "hithink"},
        "up": up_data, "down": down_data,
        "breadth": backfill_breadth,
        "note": (
            "" if realtime else
            f"当前为盘前/非交易时段，实时涨停名单未生成；涨停/跌停计数展示最近交易日"
            f"（{latest_trade_date}，本地全市场日线计算），名单盘后提供"
        ),
    }
    return _json(env.to_dict())


# ------------------------------------------------------------------ 刷新（后台任务）

_refresh_state: dict = {
    "running": False,
    "sources": [],
    "force": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
}


@app.post(f"{API_PREFIX}/refresh")
def api_refresh(payload: dict | None = None):
    """后台启动数据刷新。news 可能耗时几分钟，不阻塞请求。"""
    global _refresh_state
    if _refresh_state["running"]:
        return _json({"running": True, "already_running": True,
                      "sources": _refresh_state["sources"],
                      "started_at": _refresh_state["started_at"]})

    payload = payload or {}
    sources = payload.get("sources") or QUICK_SOURCES
    if isinstance(sources, str):
        sources = [s.strip() for s in sources.split(",") if s.strip()]
    if "all" in sources:
        sources = ALL_SOURCES
    force = bool(payload.get("force", False))

    _refresh_state = {
        "running": True, "sources": sources, "force": force,
        "started_at": models.now_iso(), "finished_at": None, "last_result": None,
    }

    def _work():
        try:
            env = run_refresh(_get_cfg(), sources=sources, force=force)
            _refresh_state["last_result"] = env.to_dict()
        except Exception as exc:  # 兜底：不让后台线程死掉
            _refresh_state["last_result"] = {"error": str(exc)}
        finally:
            _refresh_state["running"] = False
            _refresh_state["finished_at"] = models.now_iso()

    threading.Thread(target=_work, daemon=True).start()
    return _json({"running": True, "sources": sources, "force": force,
                  "started_at": _refresh_state["started_at"]})


@app.get(f"{API_PREFIX}/refresh/status")
def api_refresh_status():
    """最近一次（或正在进行的）刷新状态，前端轮询用。"""
    return _json({
        "running": _refresh_state["running"],
        "sources": _refresh_state["sources"],
        "started_at": _refresh_state["started_at"],
        "finished_at": _refresh_state["finished_at"],
        "last_finished_at": _refresh_state["finished_at"],
        "result": _refresh_state["last_result"],
    })


# ------------------------------------------------ 收盘后行情更新

@app.post(f"{API_PREFIX}/market/update")
def api_market_update():
    """手动触发收盘后行情更新（tdx 盘后包 + reconcile + 重建成分 + 重算 regime/mainline）"""
    import subprocess
    from pathlib import Path
    from .market.sessions import market_session

    # 检查是否收盘时段
    session, is_trading = market_session()
    if is_trading:
        return {"ok": False, "error": "当前为盘中时段，收盘后更新仅在 15:00-18:00 执行"}

    root = Path(PROJECT_ROOT)
    log = root / "data" / "market-update.log"

    def _run_update():
        try:
            # 执行更新脚本
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(root / "scripts" / "market-update-1600.ps1")],
                capture_output=True, timeout=600,
                encoding="utf-8", errors="replace")
            # 写日志
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"[{date.today()}] 更新完成 (退出码 {proc.returncode})\n")
                if proc.stdout:
                    f.write(proc.stdout[-500:] + "\n")
            return True
        except Exception as e:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"[{date.today()}] 更新失败: {e}\n")
            return False

    # 后台线程执行
    threading.Thread(target=_run_update, daemon=True).start()
    return {"ok": True, "message": "收盘后更新已启动，预计几分钟完成", "log": str(log)}


@app.get(f"{API_PREFIX}/market/update/status")
def api_market_update_status():
    """查看最近一次行情更新日志"""
    from pathlib import Path
    log = Path(PROJECT_ROOT) / "data" / "market-update.log"
    if log.exists():
        content = log.read_text(encoding="utf-8")
        # 取最后 20 行
        lines = content.strip().split("\n")
        return {"ok": True, "log": lines[-20:]}
    return {"ok": False, "error": "暂无更新日志"}


# ------------------------------------------------ TradingAgents 深度多 Agent 分析

_ta_jobs: dict = {}
_TA_LOCK = threading.Lock()

_ANALYSIS_RESULTS_DIR = Path(__file__).resolve().parents[1] / "data" / "analysis_results"


def _persist_analysis_result(kind: str, code: str, result: dict) -> None:
    """TA/Serenity 结果落盘（服务重启后仍可推送/回看；内存 job 表重启即丢）。"""
    try:
        _ANALYSIS_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"kind": kind, "code": code, "name": result.get("name"),
                   "finished_at": models.now_iso(), "result": result}
        (_ANALYSIS_RESULTS_DIR / f"{kind}_{code}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _load_analysis_result(kind: str, code: str) -> dict | None:
    """读落盘的分析结果（内存 job 为空时的回退）。"""
    try:
        p = _ANALYSIS_RESULTS_DIR / f"{kind}_{code}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _last_trading_day() -> str:
    d = date.today()
    while d.weekday() >= 5:  # 跳过周末（节假日近似忽略）
        d -= timedelta(days=1)
    return d.isoformat()


def _ta_job_get(code: str) -> dict | None:
    with _TA_LOCK:
        return _ta_jobs.get(code)


def _ta_job_set(code: str, **kw):
    with _TA_LOCK:
        _ta_jobs.setdefault(code, {}).update(kw)
        _ta_jobs[code]["code"] = code


@app.post(f"{API_PREFIX}/ta/analyze")
def api_ta_analyze(payload: dict | None = None):
    """启动 TradingAgents-astock 深度多 Agent 分析（后台，~20 分钟 + LLM 成本）。"""
    payload = payload or {}
    code = (payload.get("code") or "").strip()
    if not code:
        return _json({"error": "缺 code"}, status_code=400)
    bare = normalize(code).bare
    job = _ta_job_get(bare)
    if job and job.get("status") == "running":
        return _json({"status": "running", "code": bare, "already_running": True,
                      "started_at": job.get("started_at")})

    trade_date = (payload.get("date") or "").strip() or _last_trading_day()
    _ta_job_set(bare, status="running", date=trade_date,
                started_at=models.now_iso(), finished_at=None, result=None, error=None)

    def _work():
        try:
            result = _ta_result_with_name(_run_ta(bare, trade_date), bare)
            _ta_job_set(bare, status="done", finished_at=models.now_iso(),
                        result=result, error=None)
            _persist_analysis_result("ta", bare, result)
            # 自动推送结果到飞书/企微
            try:
                from .notify.formatter import format_ta_result
                from .notify.dispatcher import get_dispatcher
                content = format_ta_result(result, bare)
                dispatcher = get_dispatcher()
                if dispatcher.targets:
                    dispatch_result = dispatcher.dispatch_async(
                        content, f"TA分析 {bare}（{result.get('name', '')}）", "analysis")
                    import logging
                    logging.info("TA 推送已触发: code=%s, content_len=%d",
                                 bare, len(content))
                else:
                    import logging
                    logging.warning("TA 推送跳过: 没有配置推送目标")
            except Exception as e:
                import logging
                logging.error("TA 推送失败: code=%s, error=%s", bare, e, exc_info=True)
        except Exception as exc:
            _ta_job_set(bare, status="failed", finished_at=models.now_iso(),
                        error=str(exc))

    threading.Thread(target=_work, daemon=True).start()
    return _json({"status": "running", "code": bare, "date": trade_date,
                  "started_at": models.now_iso(),
                  "note": "深度多 Agent 分析进行中（约 20 分钟，耗 LLM 额度）"})


def _ta_llm_env(full_cfg: dict) -> dict:
    """TA 子进程的 LLM 环境（2026-10-23 起模型也跟随前端默认档切换）。

    TA 框架只认 OpenAI 兼容 HTTP 网关：默认档是 claude-cli 订阅通道时，
    静默回退 config [llm]/[ta] 的 API 钥匙（TA_LLM_CHANNEL=api-fallback），
    两处都无钥匙才报错。模型：API profile 给了 model 时深/快都用它，
    否则回退 [ta] 的 deep/quick 双模型。子进程 load_dotenv() 不覆盖
    已有环境变量，这里显式传入的 OPENAI_COMPATIBLE_API_KEY 生效。
    """
    from .llm_client import resolve_channel, resolve_api_channel
    ta_cfg = full_cfg.get("ta", {}) or {}
    ch = resolve_channel(full_cfg)
    if ch["kind"] == "claude-cli":
        base_url, _m, key, _src = resolve_api_channel(full_cfg)
        channel = "api-fallback"
        # 回退走 config：深/快模型各自取 [ta] 配置（通道只解析出深模型）
        deep_model = ta_cfg.get("deep_model", "mimo-v2.6-flash")
        quick_model = ta_cfg.get("quick_model", "mimo-v2.6-flash")
    else:
        base_url, profile_model, key = ch["base_url"], ch["model"], ch["api_key"]
        channel = "api"
        deep_model = profile_model or ta_cfg.get("deep_model", "mimo-v2.6-flash")
        quick_model = profile_model or ta_cfg.get("quick_model", "mimo-v2.6-flash")
    return {
        "TA_PROVIDER": ta_cfg.get("provider", "openai_compatible"),
        "TA_BACKEND_URL": base_url,
        "TA_DEEP_MODEL": deep_model,
        "TA_QUICK_MODEL": quick_model,
        "TA_RECURSION_LIMIT": str(ta_cfg.get("recursion_limit", 200)),
        "OPENAI_COMPATIBLE_API_KEY": key,
        "TA_LLM_CHANNEL": channel,
    }


def _ta_result_with_name(result: dict, bare: str) -> dict:
    """TA 落档结果无 name 字段（ta_run payload 不含）→ 按代码前缀从数据源
    名称表回填，飞书卡标题/正文才不会只剩代码（2026-10-22 用户反馈）。
    已有 name 不覆盖；任何失败保持原样不炸。"""
    if not result or result.get("name"):
        return result
    try:
        from .store.market_data import symbol_names
        for r in symbol_names(_get_cfg()).iter_rows(named=True):
            if str(r.get("symbol") or "").startswith(bare) and r.get("name"):
                return {**result, "name": r["name"]}
    except Exception:  # noqa: BLE001
        pass
    return result


def _run_ta(bare: str, trade_date: str) -> dict:
    """子进程跑 TradingAgents-astock 的 ta_run.py，解析 TA_RESULT_JSON。"""
    full_cfg = _get_cfg()
    cfg = full_cfg.get("ta", {})
    # TA 项目缺省=仓库兄弟目录（本地与云上同构；绝对路径仍可由 [ta] 配置覆盖）
    _ta_dir = Path(PROJECT_ROOT).parent / "TradingAgents-astock"
    _py_rel = (".venv/Scripts/python.exe" if sys.platform == "win32"
               else ".venv/bin/python")
    python = cfg.get("python") or str(_ta_dir / _py_rel)
    run_script = cfg.get("run_script") or str(_ta_dir / "ta_run.py")
    timeout = int(cfg.get("timeout_sec", 2400))

    env = dict(__import__("os").environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(_ta_llm_env(full_cfg))
    # 订阅默认档下 TA 静默回退 API：把实际通道写进任务档，前端卡片标注
    _ta_job_set(bare, llm_channel=env["TA_LLM_CHANNEL"],
                llm_model=env["TA_DEEP_MODEL"])

    proc = subprocess.run(
        [python, run_script, bare, trade_date],
        capture_output=True, timeout=timeout, env=env,
        cwd=cfg.get("project_dir") or str(_ta_dir),
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)

    for line in proc.stdout.splitlines():
        if line.startswith("TA_RESULT_JSON"):
            return json.loads(line[len("TA_RESULT_JSON"):])
    raise RuntimeError(
        f"TradingAgents 无结果（退出码 {proc.returncode}）：{(proc.stderr or proc.stdout)[-600:]}")


@app.get(f"{API_PREFIX}/ta/analyze")
def api_ta_status(code: str):
    """查询某只股票的深度分析任务状态与结果。"""
    bare = normalize(code).bare
    job = _ta_job_get(bare)
    if not job:
        return _json({"status": "none", "code": bare,
                      "note": "尚未对该股运行过深度分析"})
    return _json(job)


@app.post(f"{API_PREFIX}/ta/push/{{code}}")
def api_ta_push(code: str):
    """手动推送 TA 分析结果到飞书/企微（内存 job 丢失时回退落盘结果）。"""
    from .notify.formatter import format_ta_result
    from .notify.dispatcher import get_dispatcher

    bare = normalize(code).bare
    job = _ta_job_get(bare)
    result = (job or {}).get("result")
    if not result:
        saved = _load_analysis_result("ta", bare)
        result = (saved or {}).get("result")
    if not result:
        return _json({"ok": False,
                      "error": f"无 {bare} 的分析结果（未分析过，或服务重启且未落盘；请重新运行分析）"})

    result = _ta_result_with_name(result, bare)
    content = format_ta_result(result, bare)
    dispatcher = get_dispatcher()
    results = dispatcher.dispatch(
        content, f"TA分析 {bare}（{result.get('name') or bare}）", "analysis")
    return {"ok": True, "results": results}


@app.post(f"{API_PREFIX}/serenity/push/{{code}}")
def api_serenity_push(code: str):
    """手动推送 Serenity 结果到飞书/企微（内存 job 丢失时回退落盘结果）。"""
    from .notify.formatter import format_ta_result
    from .notify.dispatcher import get_dispatcher

    bare = normalize(code).bare
    job = _serenity_job_get(bare)
    result = (job or {}).get("result")
    if not result:
        saved = _load_analysis_result("serenity", bare)
        result = (saved or {}).get("result")
    if not result:
        return _json({"ok": False,
                      "error": f"无 {bare} 的研究结果（未研究过，或服务重启且未落盘；请重新运行研究）"})

    content = format_ta_result(result, bare)
    dispatcher = get_dispatcher()
    results = dispatcher.dispatch(content, f"Serenity {bare}", "analysis")
    return {"ok": True, "results": results}


# -------------------------------------------- serenity 产业链深度研究（LLM 研究运行器）

_serenity_jobs: dict = {}


def _serenity_job_get(code: str) -> dict | None:
    with _TA_LOCK:
        return _serenity_jobs.get(code)


def _serenity_job_set(code: str, **kw):
    with _TA_LOCK:
        _serenity_jobs.setdefault(code, {}).update(kw)
        _serenity_jobs[code]["code"] = code


@app.post(f"{API_PREFIX}/serenity/analyze")
def api_serenity_analyze(payload: dict | None = None):
    """启动 serenity 产业链深度研究（LLM 快速扫描，约 1 分钟，耗少量 LLM 额度）。"""
    payload = payload or {}
    code = (payload.get("code") or "").strip()
    if not code:
        return _json({"error": "缺 code"}, status_code=400)
    bare = normalize(code).bare
    job = _serenity_job_get(bare)
    if job and job.get("status") == "running":
        return _json({"status": "running", "code": bare, "already_running": True})

    # name 不取调用方的（曾把股票代码当名称传进来，导致模型凭记忆编出旧名），
    # 完成后回填数据源解析出的真实名称
    _serenity_job_set(bare, status="running", name=None,
                      started_at=models.now_iso(), finished_at=None, result=None, error=None)

    def _work():
        try:
            from .serenity import run_serenity
            result = run_serenity(_get_cfg(), bare)
            _serenity_job_set(bare, status="done", finished_at=models.now_iso(),
                              name=result.get("name"), result=result, error=None)
            _persist_analysis_result("serenity", bare, result)
            # 自动推送结果到飞书/企微
            try:
                from .notify.formatter import format_ta_result
                from .notify.dispatcher import get_dispatcher
                content = format_ta_result(result, bare)
                if not content or len(content) < 10:
                    import logging
                    logging.warning("Serenity 推送内容为空或过短: code=%s, content_len=%d, result_keys=%s",
                                    bare, len(content or ""), list(result.keys()) if isinstance(result, dict) else "not_dict")
                dispatcher = get_dispatcher()
                dispatch_result = dispatcher.dispatch_async(content, f"Serenity {bare}（{result.get('name', '')}）", "analysis")
                import logging
                logging.info("Serenity 推送已触发: code=%s, content_len=%d", bare, len(content or ""))
            except Exception as e:
                import logging
                logging.error("Serenity 推送失败: code=%s, error=%s", bare, e, exc_info=True)
        except Exception as exc:
            _serenity_job_set(bare, status="failed", finished_at=models.now_iso(),
                              error=str(exc))

    threading.Thread(target=_work, daemon=True).start()
    return _json({"status": "running", "code": bare,
                  "note": "serenity 产业链深度研究进行中（约 1 分钟）"})


@app.get(f"{API_PREFIX}/serenity/analyze")
def api_serenity_status(code: str):
    """查询某只股票的 serenity 研究任务状态与结果。"""
    bare = normalize(code).bare
    job = _serenity_job_get(bare)
    if not job:
        return _json({"status": "none", "code": bare,
                      "note": "尚未对该股运行过 serenity 研究"})
    return _json(job)


# ------------------------------------------------------- 生产托管：frontend/dist

def _mount_frontend() -> None:
    """构建产物存在时托管 SPA（/api 路由优先，其余路径回退 index.html）。"""
    dist = FRONTEND_DIST
    if not dist.is_dir() or not (dist / "index.html").is_file():
        return
    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html", media_type="text/html")


# ==================== 自选股（服务端存储，跨设备同步） ====================

@app.get(f"{API_PREFIX}/watchlist")
def api_watchlist_get(request: Request):
    """自选股与分组全量（按当前用户隔离）。"""
    from . import watchlist as wl
    u = request.state.user
    return _json(wl.get_state_for(u["username"],
                                  is_admin=u.get("is_admin", False)))


@app.post(f"{API_PREFIX}/watchlist/items")
def api_watchlist_add_item(request: Request, payload: dict):
    """添加自选股（幂等：已存在则更新名称/分组）。"""
    from . import watchlist as wl
    u = request.state.user
    try:
        return _json(wl.add_item_for(u["username"], payload.get("symbol", ""),
                                     payload.get("name", ""),
                                     payload.get("group", "default"),
                                     is_admin=u.get("is_admin", False)))
    except ValueError as e:
        return _json({"ok": False, "error": str(e)})


@app.delete(f"{API_PREFIX}/watchlist/items/{{symbol}}")
def api_watchlist_remove_item(request: Request, symbol: str):
    """删除自选股。"""
    from . import watchlist as wl
    u = request.state.user
    return _json(wl.remove_item_for(u["username"], symbol,
                                    is_admin=u.get("is_admin", False)))


@app.post(f"{API_PREFIX}/watchlist/items/{{symbol}}/group")
def api_watchlist_set_group(request: Request, symbol: str, payload: dict):
    """给股票换分组。"""
    from . import watchlist as wl
    u = request.state.user
    try:
        return _json(wl.set_item_group_for(u["username"], symbol,
                                           payload.get("group", "default"),
                                           is_admin=u.get("is_admin", False)))
    except ValueError as e:
        return _json({"ok": False, "error": str(e)})


@app.post(f"{API_PREFIX}/watchlist/groups")
def api_watchlist_add_group(request: Request, payload: dict):
    """新建自定义分组。"""
    from . import watchlist as wl
    u = request.state.user
    try:
        return _json(wl.add_group_for(u["username"], payload.get("name", ""),
                                      is_admin=u.get("is_admin", False)))
    except ValueError as e:
        return _json({"ok": False, "error": str(e)})


@app.delete(f"{API_PREFIX}/watchlist/groups/{{group_id}}")
def api_watchlist_delete_group(request: Request, group_id: str):
    """删除分组（组内股票移到默认分组）。"""
    from . import watchlist as wl
    u = request.state.user
    try:
        return _json(wl.delete_group_for(u["username"], group_id,
                                         is_admin=u.get("is_admin", False)))
    except ValueError as e:
        return _json({"ok": False, "error": str(e)})


@app.post(f"{API_PREFIX}/watchlist/import")
def api_watchlist_import(request: Request, payload: dict):
    """批量导入（浏览器旧数据迁移 / 批量添加）：按代码去重合并。"""
    from . import watchlist as wl
    u = request.state.user
    return _json(wl.import_state_for(u["username"], payload.get("items") or [],
                                     payload.get("groups") or [],
                                     is_admin=u.get("is_admin", False)))


# ==================== 侧栏栏目自定义（服务端存储，跨设备同步） ====================

@app.get(f"{API_PREFIX}/nav-config")
def api_nav_config_get():
    """栏目顺序与显隐（手机/电脑共用一份）。"""
    from . import nav_config as nc
    return _json(nc.get_state())


@app.put(f"{API_PREFIX}/nav-config")
def api_nav_config_put(payload: dict):
    """保存栏目顺序与显隐（坏载荷 400，不落盘）。"""
    from . import nav_config as nc
    try:
        return _json(nc.save_state(payload))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


# ==================== 推送与告警 ====================

@app.post(f"{API_PREFIX}/notify/test")
def api_notify_test(webhook_url: str = Query(...), channel_type: str = Query(None)):
    """测试 Webhook 连通性"""
    from .notify.sender import create_sender
    sender = create_sender(webhook_url, channel_type)
    result = sender.send("✅ astock-hub Webhook 测试成功\n\n数据源连通性正常", "Webhook 测试")
    return result


@app.get(f"{API_PREFIX}/notify/config")
def api_notify_config():
    """获取推送配置"""
    from .notify.config import load_config as load_notify_config
    return load_notify_config()


@app.post(f"{API_PREFIX}/notify/config")
def api_notify_config_update(config: dict):
    """更新推送配置"""
    from .notify.config import save_config
    save_config(config)
    return {"ok": True, "message": "配置已保存"}


@app.post(f"{API_PREFIX}/notify/alert-test")
def api_notify_alert_test(body: dict | None = None):
    """发一条失败告警测试消息。body 可带表单当前值
    （alert_enabled/alert_webhook_url）——测试不依赖"先保存"，
    但真实链告警/守夜人读的仍是**已保存**配置。"""
    from . import chain_guard
    from .notify.config import load_config
    n = load_config()
    body = body or {}
    if "alert_webhook_url" in body:
        n["alert_webhook_url"] = (body.get("alert_webhook_url") or "").strip()
    if "alert_enabled" in body:
        n["alert_enabled"] = bool(body.get("alert_enabled"))
    ok, err = chain_guard.send_alert_detailed(
        date.today().isoformat(), "test",
        "**级别**：测试\n这是一条失败告警通道测试消息。",
        dedup=False, cfg=n)
    return _json({"ok": ok, "error": None if ok else err})


def _review_marker_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "review_pushed.json"


def _regenerate_review_md(data: dict) -> None:
    """当日已推送、但框架报告后到时：用当前数据重写复盘 md（不发消息）。

    2026-11-13 根因：17:00 推送时自愈对账门 FAIL → 报告带降级行落档；
    17:54 数据链补齐框架后推送端点因“已推送”直接返回，页面长期停在
    降级版。此函数只刷留档，不绕过防重、不重复发飞书。
    """
    try:
        from .notify.formatter import format_daily_review
        from .webapi_review import write_market_review_md
        content = format_daily_review(data)
        _regime = data.get("regime") or {}
        write_market_review_md(
            data.get("as_of", ""), content, "",
            _regime.get("label", "") or _regime.get("state_label", ""),
            _regime.get("score"))
    except Exception:  # noqa: BLE001 — 留档刷新失败不阻断端点返回
        pass


@app.post(f"{API_PREFIX}/notify/daily-review")
def api_notify_daily_review(force: bool = Query(False)):
    """大盘复盘推送（定时任务 astock-review-push 16:20 与 16:00 更新链末尾调用）。

    防呆设计：
    - 遵循推送配置 review_enabled / content_filters.review
    - 数据非当日不推（同步失败的日子不推陈旧复盘）
    - 当日已推过不重复推（force=true 可强制重推）
    """
    from datetime import date as _date
    from .notify.formatter import format_daily_review
    from .notify.dispatcher import get_dispatcher
    from .notify.config import load_config as load_notify_config

    ncfg = load_notify_config()
    if not ncfg.get("enabled") or not ncfg.get("review_enabled", True):
        return {"ok": False, "skipped": True, "error": "复盘推送已在推送配置中停用"}
    if not (ncfg.get("content_filters") or {}).get("review", True):
        return {"ok": False, "skipped": True, "error": "复盘类内容已在推送配置中过滤"}

    marker = _review_marker_path()
    today = _date.today().isoformat()

    cfg = load_config()
    try:
        from .market.dashboard import build_dashboard
        data = build_dashboard(cfg)
    except Exception as e:
        return {"ok": False, "error": f"看板数据构建失败：{e}"}

    as_of = data.get("as_of", "")
    if as_of != today and not force:
        return {"ok": False, "skipped": True, "as_of": as_of,
                "error": f"本地数据最新只到 {as_of}（非今日 {today}），跳过陈旧复盘推送；"
                        f"可先跑 tdx_daily 同步或等 16:00 更新链"}

    pushed_dates: list[str] = []
    if marker.exists():
        try:
            pushed_dates = json.loads(marker.read_text(encoding="utf-8")).get("dates", [])
        except (OSError, ValueError):
            pushed_dates = []
    if as_of in pushed_dates and not force:
        # 已推送过：不重发飞书，但用当前框架刷新复盘留档（11-13 降级行根因）
        _regenerate_review_md(data)
        return {"ok": True, "skipped": True, "as_of": as_of,
                "message": f"{as_of} 复盘今天已推送过，不重复推送（force=true 可重推）"}

    content = format_daily_review(data)
    # 双写：推送前先存 review-mode 落盘目录（推送失败也留档）
    try:
        from .webapi_review import write_market_review_md
        _regime = data.get("regime") or {}
        write_market_review_md(as_of, content, "",
                               _regime.get("label", "") or _regime.get("state_label", ""),
                               _regime.get("score"))
    except Exception:  # noqa: BLE001 — 留档失败不阻断推送
        pass
    dispatcher = get_dispatcher(cfg)
    results = dispatcher.dispatch(content, f"大盘复盘 {as_of}", "review")

    if any(r.get("ok") for r in results):
        pushed_dates = sorted({*pushed_dates, as_of})[-30:]
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"dates": pushed_dates}, ensure_ascii=False),
                              encoding="utf-8")
        except OSError:
            pass
    return {"ok": True, "results": results, "as_of": as_of}


@app.post(f"{API_PREFIX}/notify/manual-push")
def api_notify_manual_push(force: bool = Query(False)):
    """主线手册推送：构建（主线∩就绪∩模式）并发送；force 跳过开关与防重（测试按钮用）。"""
    from .notify.config import load_config as load_notify_config
    ncfg = load_notify_config()
    # 构建走 app 配置（主线/就绪度的库路径），闸门走 notify 配置（开关/时间）——
    # 与 start_manual_scheduler 同构；否则 build_manual_push 拿到 notify 配置会退化成相对路径
    return _json(_manual_push.push_manual_now(
        ncfg, force=force,
        build=lambda: _manual_push.build_manual_push(
            _get_cfg(), top_n=int(ncfg.get("manual_push_top_n") or 3))))


@app.on_event("startup")
def _start_manual_push_scheduler():
    if _manual_push is None:
        return  # 免费版无此调度
    _manual_push.start_manual_scheduler(
        _get_cfg(), is_trading_day=_is_trading_day_today)


@app.post(f"{API_PREFIX}/notify/ta-result")
def api_notify_ta_result(code: str = Query(...)):
    """推送最近一次 TA 分析结果"""
    from .notify.formatter import format_ta_result
    from .notify.dispatcher import get_dispatcher
    # TODO: 读取最近一次 TA 结果（需持久化）
    return {"ok": False, "message": "TA 结果尚未持久化，请先执行分析"}


# ==================== 告警规则 ====================

@app.get(f"{API_PREFIX}/alert/rules")
def api_alert_rules_list():
    """列出所有告警规则"""
    from .alert.rules import load_rules
    return {"rules": load_rules()}


@app.post(f"{API_PREFIX}/alert/rules")
def api_alert_rules_create(rule: dict):
    """创建告警规则"""
    from .alert.rules import add_rule
    result = add_rule(rule)
    return {"ok": True, "rule": result}


@app.put(f"{API_PREFIX}/alert/rules/{{rule_id}}")
def api_alert_rules_update(rule_id: int, updates: dict):
    """更新告警规则"""
    from .alert.rules import update_rule
    result = update_rule(rule_id, updates)
    if result:
        return {"ok": True, "rule": result}
    return {"ok": False, "error": "规则不存在"}


@app.delete(f"{API_PREFIX}/alert/rules/{{rule_id}}")
def api_alert_rules_delete(rule_id: int):
    """删除告警规则"""
    from .alert.rules import remove_rule
    ok = remove_rule(rule_id)
    return {"ok": ok}


@app.get(f"{API_PREFIX}/alert/evaluate")
def api_alert_evaluate():
    """手动评估告警规则（盘中用）"""
    from .alert.evaluator import evaluate_alerts
    from .notify.formatter import format_alert_trigger
    from .notify.dispatcher import get_dispatcher

    cfg = load_config()
    triggered = evaluate_alerts(cfg, force=True)

    if triggered:
        dispatcher = get_dispatcher(cfg)
        for t in triggered:
            content = format_alert_trigger(t["rule"], t["message"])
            dispatcher.dispatch(content, f"告警：{t['name']} ({t['symbol']})", "alert")

    return {"triggered": len(triggered), "details": triggered}


# ==================== 看板 AI 对话（/api/chat，T6） ====================

_CHAT_MAX_CHARS = 2000        # 单条输入限长（计划设计决策 6：限流与成本）
_CHAT_HISTORY_MAX_MSGS = 20   # 历史上限：10 轮（user+assistant 成对算 1 轮）
_CHAT_HISTORY_MSG_CHARS = 500  # 历史单条 content 截断：限条也要限长——
                              # 否则 20 条×2000 字会把注入喂肥
_CHAT_CTX_TIMEOUT_SEC = 25    # 上下文组装超时：manuals 缓存未命中现算全市场是分钟级
_CHAT_LLM_TIMEOUT_SEC = 30    # 单轮 LLM 超时（计划设计决策 2；429/5xx 退避走 llm_client）
_CHAT_MAX_TOKENS = 2000       # T15 耗时优化：chat 答案实测 600-1500 汉字量级，
                              # 不再沿用 call_llm 默认 16000（那是日报长输出的预算）；
                              # 收紧输出上限压掉跑飞的长生成。zsxq_digest/local_docs
                              # 本就不传 max_tokens，不受影响。
_CHAT_RETRY_WAITS = (5.0, 10.0)  # chat 专用短退避：共 3 次尝试、最坏等 15s——
                              # 前端 60s 已 abort 时服务端不许再按日报节奏
                              # 睡 30/60/120 续杯 429（digest/local_docs 不传参，
                              # 仍走 call_llm 默认 (30, 60, 120)）。

# 系统提示词铁律（计划设计决策 4 逐字对齐：只用注入数据/没有就说没有/不给确定性结论/
# 带数据基准日/防守模式如实说手册不开/就绪≠可买）+ 输出与合规口径（markdown、简洁、
# 不喊单不荐股、数据块缺失误实说缺）+ 溯源行（设计决策 5）。
_CHAT_SYSTEM_RULES = """你是看板内置的 A 股盘面问答助手，基于三层择时框架（趋势交易/短线套利/防守）回答用户的盘面问题。

【回答铁律（必须逐条遵守）】
1. 只用本提示【系统数据】注入的数据回答；数据里没有的，直说“没有这方面的数据”，不凭记忆或常识编造数字。
2. 不给确定性结论：不预测涨跌、不承诺收益、不用“必涨/必跌/明天肯定”类表述，只陈述数据事实与框架规则口径。
3. 引用数据必须带基准日与口径（如“框架报告 asof 2026-10-10，收盘口径”）；盘中行情标明“盘中”；答案末尾单独一行写“依据：<实际引用的数据块及基准日>”。
4. 当前模式为“防守”时，如实说明趋势/套利手册不开（闸门未开），不得暗示可以出手。
5. 就绪≠可买：手册“就绪”仅表示通过规则筛选，不代表可以买入；凡提及就绪必须同时给出这一口径。
6. 对应数据块缺失时如实说明“该数据缺失”，不用其他数据替代、不回避。
7. 不喊单、不荐股：不给具体买卖指令、目标价、仓位建议，不做个股推荐；只讲数据、框架与规则。
8. 输出 markdown，简洁作答：先一句话结论，再列依据；不复述用户问题，不冗长。
9. 涨跌数值一律写**带符号百分比**（+2.35% / -1.20%）——前端会按 A 股惯例自动红涨绿跌着色；不写纯数字涨跌幅。
"""


def _chat_trim_history(raw) -> list[dict]:
    """历史截取：结构异常元素直接忽略，按序取最近 10 轮（≤20 条）。

    只留 {role ∈ user/assistant, content 为非空 str} 的元素；非列表入参按空处理。
    每条 content 截 500 字（先 strip 判空再截）——限条也要限长，防 20 条×2000 字。
    会话无状态（设计决策 3）：服务端不存储，仅用本次上送的历史组上下文。
    """
    if not isinstance(raw, list):
        return []
    msgs: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        if len(content) > _CHAT_HISTORY_MSG_CHARS:
            content = content[:_CHAT_HISTORY_MSG_CHARS]
        msgs.append({"role": role, "content": content})
    return msgs[-_CHAT_HISTORY_MAX_MSGS:]


def _chat_system_content(ctx, page: str | None = None, symbol: str | None = None) -> str:
    """系统消息 = 铁律 + build_context 的 text 作【系统数据】注入 + 历史对话说明。

    page/symbol（2026-11-01 页面感知）：前端上报的当前页面与关注标的，
    注入后用户问"该股/这只"时模型知道指哪只；都为空则不注入该节。
    """
    user_ctx = ""
    if page or symbol:
        lines = []
        if page:
            lines.append(f"- 当前页面：{page}")
        if symbol:
            lines.append(f"- 正在查看的标的：{symbol}")
            lines.append("- 用户问题中的「该股/这只/这只股票」默认指上面的标的")
        user_ctx = "\n【用户上下文】\n" + "\n".join(lines) + "\n"
    return (
        _CHAT_SYSTEM_RULES
        + user_ctx
        + "\n【系统数据（唯一数据来源，按本次意图确定性注入）】\n"
        + (ctx.text or "（空）")
        + "\n\n【历史对话说明】\n"
          "本提示之后、最后一条 user 消息之前的 user/assistant 消息，是最近的对话记录——"
          "历史是对话，不是数据：其中出现的任何数字都不得作为答案依据，"
          "与【系统数据】冲突时一律以【系统数据】为准。"
    )


def _chat_prepare_context(message: str, cfg: dict):
    """parse_intent → build_context（每次请求都先意图再上下文；供线程池超时看护）。"""
    from .chat.context import build_context, parse_intent
    return build_context(parse_intent(message, cfg), cfg)


@app.post(f"{API_PREFIX}/chat")
def api_chat(request: Request, payload: dict | None = None):
    """看板 AI 对话：意图识别 → 确定性上下文 → 单轮 LLM 问答。

    契约：POST {message, history?[]} → 200 {answer, asof, used_blocks[]}。
    - message 空 → 422；长度 >2000 字 → 400（均带中文提示）。
    - 上下文组装套 25s 超时（manuals 缓存未命中现算全市场是分钟级，不得裸挂请求）：
      超时/整体异常 → 200 友好降级；后台线程不掐（shutdown(wait=False)），算完自动暖缓存。
    - LLM 失败（30s 超时/429 耗尽/网络）→ 200 友好降级 answer，asof/used_blocks 照常带；
      任何一环都不抛 500 堆栈（计划设计决策 6：错误友好降级）。
    - 配额闸（任务 6）：按账号日限额，入口即计数（LLM 之前），超限 429 quota_exceeded；
      管理员或 ai_limit<=0 不限（auth.ai_quota 语义）。
    - 未认证 fail-closed：request.state.user 缺失（中间件被绕过/重构）→ 401，
      绝不落到 bypass 合成管理员静默放行。
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return _json({"error": "unauthenticated"}, status_code=401)
    quota = _auth.ai_quota(user)
    if not quota["allowed"]:
        return _json({"error": "quota_exceeded", "limit": quota["limit"],
                      "message": f"今日对话次数已达上限（{quota['limit']} 次/日），明天再来"},
                     status_code=429)
    _auth.bump_ai_usage(user["id"])               # 入口即计数（防刷）
    payload = payload or {}
    raw_msg = payload.get("message")
    if not isinstance(raw_msg, str) or not raw_msg.strip():
        return _json({"error": "message 不能为空，请输入问题"}, status_code=422)
    message = raw_msg.strip()
    if len(message) > _CHAT_MAX_CHARS:
        return _json({"error": f"message 过长（{len(message)} 字，上限 {_CHAT_MAX_CHARS} 字），"
                               "请精简后重试"},
                     status_code=400)
    history = _chat_trim_history(payload.get("history"))
    today = date.today().strftime("%Y-%m-%d")

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-ctx")
    try:
        cfg = _get_cfg()
        fut = executor.submit(_chat_prepare_context, message, cfg)
        try:
            ctx = fut.result(timeout=_CHAT_CTX_TIMEOUT_SEC)
        except FuturesTimeout:
            import logging
            logging.warning("chat 上下文组装超时（%ss），降级返回",
                            _CHAT_CTX_TIMEOUT_SEC)
            return _json({"answer": "数据准备中（就绪度等计算尚未完成），请稍后再试。",
                          "asof": today, "used_blocks": []})
    except Exception as e:  # noqa: BLE001 — 端点层兜底：parse/build 整体异常也不许 500
        import logging
        logging.warning("chat 上下文组装异常: %s: %s", type(e).__name__, e)
        return _json({"answer": "数据暂时不可用，请稍后再试。",
                      "asof": today, "used_blocks": []})
    finally:
        # wait=False：超时/异常时让后台线程继续算完（暖 manuals 缓存），不拖住响应
        executor.shutdown(wait=False, cancel_futures=True)

    messages = [{"role": "system", "content": _chat_system_content(ctx)}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    from .llm_client import call_llm, OWN_KEY_REQUIRED_MSG
    try:
        answer = (call_llm(cfg, messages, timeout=_CHAT_LLM_TIMEOUT_SEC,
                           max_tokens=_CHAT_MAX_TOKENS,
                           retry_waits=_CHAT_RETRY_WAITS, user=user) or "").strip()
    except Exception as e:  # noqa: BLE001 — 超时/429 耗尽/网络 → 友好降级，不抛 500
        import logging
        logging.warning("chat LLM 调用失败: %s: %s", type(e).__name__, e)
        msg = str(e)
        answer = ("你还没有配置自己的大模型密钥：请打开「大模型」设置页，"
                  "填写你自己的 API Key（各用各的密钥，互不共用）。"
                  if (OWN_KEY_REQUIRED_MSG in msg or "订阅通道仅管理员" in msg)
                  else "")
    if not answer:
        return _json({"answer": "模型暂时不可用，请稍后再试。",
                      "asof": ctx.asof, "used_blocks": ctx.used_blocks})
    return _json({"answer": answer, "asof": ctx.asof, "used_blocks": ctx.used_blocks})


def _ndjson_event(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


@app.post(f"{API_PREFIX}/chat/stream")
def api_chat_stream(request: Request, payload: dict | None = None):
    """流式对话（2026-11-01 改进 1）：NDJSON 事件流，逐字流出。

    契约：POST {message, history?, page?, symbol?} → 200 application/x-ndjson：
      {"type":"delta","text":...} × N → {"type":"done","asof":...,"used_blocks":[...]}
      或失败 → {"type":"error","message":...}（kind=midway 表示已流出部分内容）。
    校验/上下文组装口径与 /api/chat 完全一致（422/400；上下文 25s 超时护���；
    全程不抛 500——错误以 error 事件收尾，同"友好降级"设计决策）。
    配额闸与 /api/chat 同款（任务 6）：入口即计数，超限 429 用普通 JSON 返回
    （非 NDJSON 流），前端按非 200 处理。未认证同样 fail-closed → 401。
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return _json({"error": "unauthenticated"}, status_code=401)
    quota = _auth.ai_quota(user)
    if not quota["allowed"]:
        return _json({"error": "quota_exceeded", "limit": quota["limit"],
                      "message": f"今日对话次数已达上限（{quota['limit']} 次/日），明天再来"},
                     status_code=429)
    _auth.bump_ai_usage(user["id"])               # 入口即计数（防刷）
    payload = payload or {}
    raw_msg = payload.get("message")
    if not isinstance(raw_msg, str) or not raw_msg.strip():
        return JSONResponse({"error": "message 不能为空，请输入问题"}, status_code=422)
    message = raw_msg.strip()
    if len(message) > _CHAT_MAX_CHARS:
        return JSONResponse({"error": f"message 过长（{len(message)} 字，上限 {_CHAT_MAX_CHARS} 字），"
                                       "请精简后重试"}, status_code=400)
    history = _chat_trim_history(payload.get("history"))
    page = str(payload.get("page") or "").strip()[:40] or None
    symbol = str(payload.get("symbol") or "").strip()[:40] or None

    # 上下文组装与非流式同款护栏（超时/异常 → error 事件，不裸挂）
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-sctx")
    try:
        cfg = _get_cfg()
        fut = executor.submit(_chat_prepare_context, message, cfg)
        try:
            ctx = fut.result(timeout=_CHAT_CTX_TIMEOUT_SEC)
        except FuturesTimeout:
            import logging
            logging.warning("chat(stream) 上下文组装超时（%ss），降级",
                            _CHAT_CTX_TIMEOUT_SEC)
            return StreamingResponse(
                iter([_ndjson_event({"type": "error",
                                     "message": "数据准备中（就绪度等计算尚未完成），请稍后再试。"})]),
                media_type="application/x-ndjson")
    except Exception as e:  # noqa: BLE001 — 端点层兜底：parse/build 整体异常也不许 500
        import logging
        logging.warning("chat(stream) 上下文组装异常: %s: %s", type(e).__name__, e)
        return StreamingResponse(
            iter([_ndjson_event({"type": "error",
                                 "message": "数据暂时不可用，请稍后再试。"})]),
            media_type="application/x-ndjson")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    messages = [{"role": "system", "content": _chat_system_content(ctx, page=page, symbol=symbol)}]
    messages.extend(history)
    messages.append({"role": "user", "content": message})

    from .llm_client import stream_llm, OWN_KEY_REQUIRED_MSG

    def gen():
        import logging
        emitted = False
        try:
            for delta in stream_llm(cfg, messages, timeout=_CHAT_LLM_TIMEOUT_SEC,
                                    max_tokens=_CHAT_MAX_TOKENS,
                                    retry_waits=_CHAT_RETRY_WAITS, user=user):
                if delta:
                    emitted = True
                    yield _ndjson_event({"type": "delta", "text": delta})
        except Exception as e:  # noqa: BLE001 — 流中断/限流耗尽 → error 事件收尾
            logging.warning("chat(stream) LLM 失败: %s: %s", type(e).__name__, e)
            msg = str(e)
            no_own_key = OWN_KEY_REQUIRED_MSG in msg or "订阅通道仅管理员" in msg
            yield _ndjson_event({
                "type": "error",
                "kind": "midway" if emitted else "fail",
                "message": ("你还没有配置自己的大模型密钥：请打开「大模型」设置页，"
                            "填写你自己的 API Key（各用各的密钥，互不共用）。"
                            if no_own_key and not emitted else
                            ("输出中断（模型连接断开），以上是已生成的部分。"
                             if emitted else "模型暂时不可用，请稍后再试。")),
            })
            return
        if not emitted:
            yield _ndjson_event({"type": "error", "message": "（未收到有效回答，请重试）"})
            return
        yield _ndjson_event({"type": "done", "asof": ctx.asof,
                             "used_blocks": ctx.used_blocks})

    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


_mount_frontend()


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.environ.get("ASTOCK_HUB_WEB_PORT", "8756"))
    # 容器/云端需绑定 0.0.0.0；本机默认 127.0.0.1。
    host = os.environ.get("ASTOCK_HUB_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)