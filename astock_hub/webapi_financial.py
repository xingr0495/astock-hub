"""财务报表与龙虎榜 API 端点。

路由前缀: /api/financial /api/dragon-tiger/stock

端点:
  GET /api/financial/{code}      - 财务报表数据
  GET /api/dragon-tiger/stock/{code} - 个股龙虎榜
"""

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api", tags=["financial"])


@router.get("/financial/{code}")
def get_financial(code: str, limit: int = Query(10, ge=1, le=50)):
    """获取个股财务报表数据（业绩预告 + 研报 + 估值）。"""
    from astock_hub.adapters.astock_data import AStockDataAdapter
    from astock_hub.adapters.hithink import HithinkAdapter
    from astock_hub.config import load_config

    cfg = load_config()
    asd = AStockDataAdapter(cfg.get("a_stock_data", {}))
    ht = HithinkAdapter(cfg.get("hithink", {}))

    result = {
        "code": code,
        "earnings": None,    # 业绩预告
        "reports": None,     # 研报
        "valuation": None,   # 估值
    }

    # 1. 业绩预告
    try:
        r = asd.earnings_forecast(code)
        if r.status == "ok" and r.data:
            result["earnings"] = r.data.get("item", [])[:limit]
    except Exception as e:
        result["earnings_error"] = str(e)

    # 2. 研报
    try:
        r = asd.eastmoney_reports(code)
        if r.status == "ok" and r.data:
            result["reports"] = r.data.get("item", [])[:limit]
    except Exception as e:
        result["reports_error"] = str(e)

    # 3. 估值（iFinD 已停用——额度耗尽，2026-10-08 用户决策暂时全面停用 → hithink）
    try:
        from astock_hub.symbols import normalize
        thscode = normalize(code).ts_code
        r = ht.valuation_thscodes([thscode])
        if r.status == "ok":
            items = r.data.get("item", [])
            if items:
                result["valuation"] = items[0]
    except Exception as e:
        result["valuation_error"] = str(e)

    return {"ok": True, "data": result}


@router.get("/dragon-tiger/stock/{code}")
def get_stock_dragon_tiger(code: str, days: int = Query(10, ge=1, le=30)):
    """获取个股龙虎榜数据。"""
    from astock_hub.adapters.astock_data import AStockDataAdapter
    from astock_hub.config import load_config

    cfg = load_config()
    asd = AStockDataAdapter(cfg.get("a_stock_data", {}))

    try:
        r = asd.dragon_tiger(code)
        # ok/stale 都放行：龙虎榜是记录型数据（as_of=实际上榜日），老记录
        # 被新鲜度标 stale 属正常——前端按"近N次记录"渲染，不该被拦。
        if r.status in ("ok", "stale") and r.data:
            items = r.data.get("item", [])
            return {"ok": True, "data": {"items": items[:days], "total": len(items)}}
        return {"ok": False, "data": {"items": [], "total": 0}}
    except Exception as e:
        return {"ok": False, "error": str(e)}
