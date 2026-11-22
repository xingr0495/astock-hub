"""连板梯队逐层概念/行业分布（B2-6）。

读取每日涨停池存档，成分映射复用 hithink membership；属性/状态类概念沿用
主线黑名单，不把“昨日涨停、融资融券”等假板块计入分布。纯收盘口径。
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ..symbols import normalize
from .mainline import ATTRIBUTE_SECTOR_BLACKLIST


def _pool_path(cfg: dict, day: str) -> Path:
    root = Path(cfg.get("hithink_data", {}).get("store_dir") or "data") / "limit_pool"
    return root / f"{day}.json"


def _latest_pool_day(cfg: dict) -> str | None:
    root = Path(cfg.get("hithink_data", {}).get("store_dir") or "data") / "limit_pool"
    days = [p.stem for p in root.glob("????-??-??.json")]
    return max(days) if days else None


def _is_attribute_concept(name: str) -> bool:
    # 黑名单之外还有“2026年报预增”这类带年份的动态属性桶。
    return name in ATTRIBUTE_SECTOR_BLACKLIST or any(
        marker in name for marker in ("年报预", "季报预", "中报预", "业绩预"))


def _membership(cfg: dict, kind: str, symbols: set[str]) -> dict[str, list[str]]:
    from ..store.concepts import concept_map_df
    df, _ = concept_map_df(cfg, kind)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("_sym_up").is_in(symbols))
    out: dict[str, list[str]] = {}
    for row in df.iter_rows(named=True):
        name = row.get(kind)
        if not name or (kind == "concept" and _is_attribute_concept(name)):
            continue
        out.setdefault(row["_sym_up"], []).append(name)
    return out


def limit_ladder(cfg: dict, day: str | None = None) -> dict:
    target = day or _latest_pool_day(cfg)
    if not target:
        return {"available": False, "reason": "no_data", "layers": []}
    path = _pool_path(cfg, target)
    if not path.exists():
        return {"available": False, "as_of": target, "reason": "no_data", "layers": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "as_of": target, "reason": "invalid_data", "layers": []}

    stocks = doc.get("zt") or []
    symbols = {normalize(str(s.get("code") or "")).ts_code for s in stocks if s.get("code")}
    maps = {kind: _membership(cfg, kind, symbols) for kind in ("concept", "industry")}
    layers: dict[int, dict] = {}
    for stock in stocks:
        try:
            boards = int(stock.get("limit_days") or 1)
            symbol = normalize(str(stock.get("code") or "")).ts_code
        except (TypeError, ValueError):
            continue
        concepts = maps["concept"].get(symbol, [])
        industries = maps["industry"].get(symbol, [])
        if not industries and stock.get("industry"):
            industries = [stock["industry"]]
        layer = layers.setdefault(boards, {"limit_days": boards, "stocks": []})
        layer["stocks"].append({
            "symbol": symbol,
            "name": stock.get("name") or symbol,
            "concepts": concepts,
            "industries": industries,
        })

    result = []
    for boards in sorted(layers, reverse=True):
        layer = layers[boards]
        entry = {"limit_days": boards, "total": len(layer["stocks"]),
                 "stocks": sorted(layer["stocks"], key=lambda s: s["symbol"])}
        for kind in ("concept", "industry"):
            counts: dict[str, int] = {}
            key = "concepts" if kind == "concept" else "industries"
            for stock in layer["stocks"]:
                for name in set(stock[key]):
                    counts[name] = counts.get(name, 0) + 1
            entry[key] = [{"name": n, "count": c} for n, c in
                          sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
        result.append(entry)
    return {"available": bool(result), "as_of": target,
            "total": len(stocks), "layers": result,
            "note": "涨停池收盘口径；概念剔除属性/状态类假板块"}
