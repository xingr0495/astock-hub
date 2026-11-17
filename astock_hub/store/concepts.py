"""概念/行业成分映射的提供方：股票 → 所属概念/行业。

情绪主线（mainline）与板块轮动（rps）都需要"股票 → 维度成员"这张映射。tickflow 读
它自己的 ext_gn_ths 快照；astock-hub 用同花顺概念/行业指数目录 + 成分替代：

- `HithinkAdapter.index_catalog(tag)`：cn_concept（390 个概念）/ industry（320 个行业）
- `HithinkAdapter.constituents(thscode)`：某个概念/行业指数的成分股

首次构建要对每个指数取一次成分（几百次 CLI 调用），用有限并发跑（4-6 个，避免
打爆远端）；结果落盘成 parquet 缓存，之后直接读。成分快照无历史版本——今天的成分
回看历史，早年归属必然漂移，这一限制会随结果一起暴露给调用方（MEMBERSHIP_NOTE）。

已知差异：同花顺行业指数目录是扁平名称（"种子生产"），没有 tickflow 那种
"一级-二级-三级"的层级路径，因此 RPS 的行业 `level` 参数在本实现里不支持（按全名
聚合）。
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from .. import models
from ..adapters.hithink import HithinkAdapter

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_FETCH_WORKERS = 5
_CACHE_TTL_DAYS = 1  # 成分每日刷新即可（摘帽/成分调整频率低）

# kind → 同花顺指数目录 tag
_TAGS = {"concept": "cn_concept", "industry": "industry"}

MEMBERSHIP_NOTE = (
    "概念/行业成分为当前快照回看历史（无历史版本），早年归属存在漂移，越近越准"
)


def _cache_dir(cfg: dict) -> Path:
    base = Path(cfg.get("hithink_data", {}).get("cache_dir") or "data/hithink_cache")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _adapter(cfg: dict) -> HithinkAdapter:
    return HithinkAdapter(cfg.get("hithink_data", {}) or cfg.get("hithink", {}))


def _cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now(_CST).date() - datetime.fromtimestamp(
        path.stat().st_mtime, tz=_CST).date()
    return age.days < _CACHE_TTL_DAYS


def concept_map_df(cfg: dict, kind: str = "concept", *, force: bool = False
                   ) -> tuple[pl.DataFrame, int]:
    """构建/读取 {_sym_up(大写股票代码) → 成员名} 的展开映射。

    返回 (map_df, member_count)：map_df 两列 `_sym_up` 与 `<kind>`，一只股票属于
    多个概念时是多行；member_count 是去重成员（概念/行业）总数。
    """
    if kind not in _TAGS:
        raise ValueError(f"kind 只支持 {sorted(_TAGS)}，收到 {kind!r}")

    cache = _cache_dir(cfg) / f"membership_{kind}.parquet"
    if not force and _cache_fresh(cache):
        df = pl.read_parquet(cache)
        return df, df[kind].n_unique()

    ht = _adapter(cfg)
    catalog = ht.index_catalog(_TAGS[kind])
    if not catalog:
        raise RuntimeError(f"取{kind}目录失败（远端可能不可用）")

    def _members(entry: dict) -> tuple[str, list[str]]:
        name = entry.get("name") or ""
        res = ht.constituents(entry.get("thscode"))
        if res.status != models.OK:
            return name, []
        items = (res.data or {}).get("item") or []
        return name, [str(i.get("thscode", "")).upper() for i in items
                      if i.get("thscode")]

    pairs: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        for name, syms in pool.map(lambda e: _members(e), catalog):
            pairs += [(s, name) for s in syms]

    if not pairs:
        raise RuntimeError(f"{kind}成分全部取失败（远端或 CLI 不可用）")

    df = pl.DataFrame({"_sym_up": [p[0] for p in pairs],
                       kind: [p[1] for p in pairs]}).unique()
    # tmp 名带 PID：16:00/17:45 链与手动重建并发时若共用同一 .part.parquet，
    # 两个写进程字节交错 → 最终文件非法 parquet（2026-11-08 实测
    # membership_concept.parquet "Unexpected struct field type 15" 看板全挂）。
    # 各写各的临时文件 + rename 原子换入，最后完成者覆盖，恒为完整文件。
    tmp = cache.with_suffix(f".{os.getpid()}.part.parquet")
    df.write_parquet(tmp)
    tmp.replace(cache)
    logger.info("membership(%s): %d 只股票 × %d 个成员，%d 条归属",
                kind, df["_sym_up"].n_unique(), df[kind].n_unique(), df.height)
    return df, df[kind].n_unique()


def all_membership(cfg: dict, *, force: bool = False) -> dict:
    """一次性构建概念+行业两张映射（首次全量构建入口），返回构建摘要。"""
    summary = {}
    for kind in _TAGS:
        df, count = concept_map_df(cfg, kind, force=force)
        summary[kind] = {"members": count, "stocks": df["_sym_up"].n_unique(),
                         "pairs": df.height}
    return summary


def membership_lookup(cfg: dict, kind: str) -> dict[str, list[str]]:
    """ts_code（大写）→ 成员名列表（按映射行序、去重保序）。

    叠加层用途（纪要池概念列等）：取数/缓存失败一律返回 {}，不拖垮调用方。
    """
    try:
        df, _ = concept_map_df(cfg, kind)
    except Exception as e:  # noqa: BLE001
        logger.warning("membership_lookup(%s) 失败：%s", kind, e)
        return {}
    out: dict[str, list[str]] = {}
    for sym, name in df.select(["_sym_up", kind]).iter_rows():
        lst = out.setdefault(sym, [])
        if name not in lst:
            lst.append(name)
    return out


def concept_view(concepts: list[str], industries: list[str],
                 mainline: dict[str, str]) -> dict:
    """一只股票的概念展示视图：行业单列、命中当下主线的概念排最前。

    mainline = {概念名: 看板五分类（主线/次主线）}，按主线 rank 排序后传入，
    输出 mainline_concepts 保持该顺序并附 mainline_cls 供前端分色。
    """
    cs = list(dict.fromkeys(concepts or []))
    inds = list(dict.fromkeys(industries or []))
    hit = [name for name in mainline if name in cs]
    hitset = set(hit)
    rest = [c for c in cs if c not in hitset]
    return {"industry": inds[0] if inds else None,
            "mainline_concepts": hit,
            "mainline_cls": {k: mainline[k] for k in hit},
            "concepts": hit + rest}
