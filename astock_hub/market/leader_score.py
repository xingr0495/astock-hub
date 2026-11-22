"""板块龙头六因子评分与热度/风险拆解（B3，对齐 TSP 公式）。

六因子：动能35%/换手22%/成交额18%/市值15%/量比7%/连板3%。
缺失因子不做中性补值：贡献记 0、返回 coverage 与 missing，页面必须提示分数偏低。
"""
from __future__ import annotations

import math
import time

import polars as pl

WEIGHTS = {
    "momentum": 0.35, "turnover": 0.22, "amount": 0.18,
    "cap": 0.15, "volume": 0.07, "boards": 0.03,
}
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 60


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v)) if math.isfinite(v) else 0.0


def _num(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def leader_score(stock: dict) -> dict:
    pct = _num(stock.get("change_pct"))
    turnover = _num(stock.get("turnover_rate"))
    amount = _num(stock.get("amount"))
    cap = _num(stock.get("float_market_cap")) or _num(stock.get("market_cap"))
    vol_ratio = _num(stock.get("vol_ratio_5d"))
    boards = _num(stock.get("consecutive_limit_ups"))
    raw = {"momentum": pct, "turnover": turnover, "amount": amount,
           "cap": cap, "volume": vol_ratio, "boards": boards}
    parts = {
        "momentum": _clamp((pct + 0.02) / 0.12) if pct is not None else None,
        "turnover": _clamp(math.log1p(max(turnover, 0)) / math.log1p(30)) if turnover is not None else None,
        "amount": _clamp(math.log1p(max(amount, 0)) / math.log1p(20_000_000_000)) if amount is not None else None,
        "cap": _clamp(math.log1p(max(cap, 0)) / math.log1p(300_000_000_000)) if cap is not None else None,
        "volume": _clamp((vol_ratio - 1) / 4) if vol_ratio is not None else None,
        "boards": _clamp(boards / 5) if boards is not None else None,
    }
    score = sum(parts[k] * w for k, w in WEIGHTS.items() if parts[k] is not None) * 100
    coverage = sum(w for k, w in WEIGHTS.items() if parts[k] is not None)
    return {"score": round(score, 2), "parts": parts, "raw": raw,
            "coverage": round(coverage, 2),
            "missing": [k for k, v in parts.items() if v is None]}


def sector_score_rows(rows: list[dict]) -> dict:
    scored = []
    for row in rows:
        scored.append({"symbol": row.get("symbol"), "name": row.get("name"),
                       **leader_score(row)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    pcts = [_num(r.get("change_pct")) for r in rows]
    pcts = [v for v in pcts if v is not None]
    amounts = [_num(r.get("amount")) or 0 for r in rows]
    total_amount = sum(amounts)
    avg_pct = sum(pcts) / len(pcts) if pcts else 0
    up_rate = sum(v > 0 for v in pcts) / len(pcts) if pcts else 0
    strong = sum(v >= 0.05 for v in pcts)
    weak = sum(v <= -0.05 for v in pcts)
    n = len(rows)
    amount_score = _clamp(math.log1p(total_amount) / math.log1p(80_000_000_000))
    strong_score = _clamp(strong / max(1, n * 0.18)) if n else 0
    leader_part = _clamp((scored[0]["score"] if scored else 0) / 100)
    avg_part = _clamp((avg_pct + 0.02) / 0.09)
    up_part = _clamp((up_rate - 0.35) / 0.55)
    heat = (avg_part * 0.38 + up_part * 0.20 + strong_score * 0.16
            + amount_score * 0.12 + leader_part * 0.14) * 100
    risk = (_clamp((-avg_pct + 0.01) / 0.08) * 0.55
            + _clamp(weak / max(1, n * 0.18)) * 0.45) * 100
    return {"rows": scored, "heat_score": round(heat, 1), "risk_score": round(risk, 1),
            "avg_pct": avg_pct, "up_rate": up_rate, "total_amount": total_amount,
            "leader": scored[0] if scored else None}


def sector_leader_scores(cfg: dict, kind: str, name: str) -> dict:
    from ..adapters.tencent import TencentAdapter
    from ..store import market_data as md
    from ..store.concepts import concept_map_df
    from . import dashboard

    end = md.db_max_date(cfg)
    key = (kind, name, end)
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    map_df, _ = concept_map_df(cfg, kind)
    symbols = map_df.filter(pl.col(kind) == name)["_sym_up"].unique().to_list()
    frame = dashboard._base_frame(cfg, end).filter(
        pl.col("date") == pl.lit(end).str.to_date("%Y-%m-%d"),
        pl.col("symbol").is_in(symbols))
    rows = {r["symbol"]: dict(r) for r in frame.iter_rows(named=True)}
    name_of = {r["symbol"]: r["name"] for r in md.symbol_names(cfg).iter_rows(named=True)}

    # 换手与市值不在本地日线库，取腾讯快照；失败时字段留空、覆盖率降级。
    quote_of = {}
    quote_error = None
    try:
        result = TencentAdapter(cfg.get("tencent", {})).snapshot_chunked(symbols)
        if result.status == "ok":
            quote_of = {r["symbol"]: r for r in (result.data or {}).get("item", [])}
        else:
            quote_error = result.error
    except Exception as exc:  # noqa: BLE001
        quote_error = str(exc)

    merged = []
    for symbol in symbols:
        base = rows.get(symbol, {"symbol": symbol})
        quote = quote_of.get(symbol, {})
        merged.append({**base,
                       "symbol": symbol, "name": name_of.get(symbol) or quote.get("name"),
                       "turnover_rate": quote.get("turnover_ratio"),
                       "float_market_cap": quote.get("float_market_cap"),
                       "market_cap": quote.get("market_cap")})
    result = sector_score_rows(merged)
    result.update({"available": bool(merged), "as_of": end, "kind": kind, "name": name,
                   "total": len(merged), "quote_error": quote_error,
                   "note": "六因子贡献按 TSP 原公式；缺失因子贡献为0，覆盖率<100%时分数偏低"})
    _CACHE[key] = (time.monotonic(), result)
    return result
