# -*- coding: utf-8 -*-
"""AI 要点层（收编自 investment-news/scripts/digest.py，2026-10-24）。

读 news.json，对每个行业生成「今日要点」+ 每条新闻中文标题翻译 + 一句话中文
摘要，写回 news.json；翻译缓存独立存 zh_cache.json（已翻过的不重烧）。

2026-10-08 口径（行为冻结）：
- 覆盖范围 TOPN 40（每栏最新 40 条）
- 增量翻译：标题→{zh,zs} 存缓存，只翻新增条目
- 每条除中文标题(zh)外增一句话中文摘要(zh_summary)
- 伪翻译防护：zh==原文视为未翻；LLM 输出不覆盖已有有效中文标题
- 失败栏隔离：单栏 LLM 失败重试 ATTEMPTS 次后要点留空，不拖垮其他栏

LLM 走 hub 统一通道 call_llm（api 档 WORKERS=3 并发；claude-cli 订阅档
强制 WORKERS=1 串行——每栏一次 CLI 子进程，并发起多会拖垮机器）。
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor

from .. import llm_client
from . import store

TOPN = 40            # 每行业保留最新 N 条（要点+翻译都基于它）
ATTEMPTS = 3         # 单栏 LLM 失败重试上限
ZH_CACHE: dict = {}  # run_digest 里加载

SYS = ("你是中文行业新闻分析助手。给你某行业最近的新闻列表(每条带序号,带 [需翻译] 标记的是待翻条目),请做两件事:\n"
       "1) 提炼 3-5 条「今日要点」:聚合最重要的行业动向,以最新条目为主,每条不超过 40 字,可合并同类、突出数字/公司/趋势,客观陈述。"
       "每条要点用 refs 标注它主要来自哪几条新闻的序号(用于跳转原文,至少 1 个)。\n"
       "2) 只翻译带 [需翻译] 标记的新闻:给出简洁准确的中文标题(zh)和不超过 40 字的一句话中文摘要(zs);若原文已是中文则原样返回。\n"
       "只输出 JSON,不要任何解释或代码块标记。格式:\n"
       '{"points":[{"t":"要点1","refs":[0,3]},{"t":"要点2","refs":[5]}],"items":[{"i":0,"zh":"中文标题","zs":"中文摘要"}]}')


def _norm_title(t):
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _has_cjk(s):
    return bool(re.search(r"[一-鿿]", s or ""))


def _valid_zh(it):
    """已有 zh 是否算有效翻译：非空，且（与原标题不同 或 原标题本身是中文）。
    zh==英文原标题 视为未翻译（LLM 偶发把原文抄进 zh）。"""
    zh = (it.get("zh") or "").strip()
    if not zh:
        return False
    return _norm_title(zh) != _norm_title(it.get("title")) or _has_cjk(it.get("title"))


def _workers() -> int:
    """api 档 3 并发；claude-cli 订阅档串行（每栏一次 CLI 子进程）。"""
    try:
        from ..credentials import get_llm_profile
        p = get_llm_profile(None)
        if isinstance(p, dict) and p.get("kind") == "claude-cli":
            return 1
    except Exception:  # noqa: BLE001
        pass
    return 3


def _llm_once(user, label, cfg):
    """单次 LLM 调用。重试交给 process 的 ATTEMPTS，这里传空退避序列不双层等。"""
    try:
        return llm_client.call_llm(
            cfg,
            [{"role": "system", "content": SYS},
             {"role": "user", "content": user}],
            timeout=240, max_tokens=4000, temperature=0.3,
            retry_waits=())
    except Exception as e:   # 不再静默吞掉:打出原因(超时/空返回/格式异常)
        print("  ⚠️ LLM 调用异常 [%s]: %s" % (label, str(e)[:120]))
        return ""


def extract_json(s):
    if not s:
        return None
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < 0:
        return None
    try:
        return json.loads(s[a:b + 1])
    except Exception:  # noqa: BLE001
        return None


def process(ind, cfg):
    items = ind["items"][:TOPN]
    ind["items"] = items                       # 先统一截到 TOPN,失败栏也与成功栏保持一致条数
    if not items:
        ind["points"] = ind.get("points") or []
        return ind

    # 1) 翻译缓存命中先填（news.json 每日重写，缓存独立于它存活）；
    #    news.json 里已有的有效翻译采纳进缓存，不重复烧大模型
    todo = []
    updates_pre = []
    for i, it in enumerate(items):
        key = _norm_title(it.get("title"))
        hit = ZH_CACHE.get(key)
        if hit:
            it["zh"] = hit.get("zh", "")
            it["zh_summary"] = hit.get("zs", "")
        elif _valid_zh(it):
            if it.get("zh_summary"):
                updates_pre.append((key, {"zh": it["zh"].strip(),
                                          "zs": it["zh_summary"].strip()}))
            else:
                todo.append(i)          # 有标题翻译，只补摘要（不覆盖已有 zh）
        else:
            it["zh"] = ""               # 清掉"zh==英文原文"的伪翻译，重翻
            todo.append(i)
    need_points = not ind.get("points")
    if not todo and not need_points:
        ind["_zh_updates"] = updates_pre
        return ind                             # 全部已翻且要点已有：零大模型开销

    # 2) LLM：要点基于全部条目上下文，翻译只翻未命中项
    lines = ["行业:%s" % ind["name"], "新闻:"]
    for i, it in enumerate(items):
        mark = " [需翻译]" if i in todo else ""
        lines.append("%d. %s (%s)%s" % (i, it["title"], it.get("source", ""), mark))
    user = "\n".join(lines)
    d = None
    for _ in range(ATTEMPTS):                   # 偶发失败重试(总计 ATTEMPTS 次)
        d = extract_json(_llm_once(user, ind["name"], cfg))
        if d:
            break
    if not d:
        print("  ⚠️ %s 生成失败(已试 %d 次),本栏要点/新条目翻译留空,重跑可只补本栏" % (ind["name"], ATTEMPTS))
        ind["points"] = ind.get("points") or []
        return ind

    if need_points:
        pts = []
        for p in d.get("points", [])[:5]:
            if isinstance(p, dict):
                t = (p.get("t") or "").strip()
                url = ""
                for r in p.get("refs", []):
                    if isinstance(r, int) and 0 <= r < len(items) and items[r].get("url"):
                        url = items[r]["url"]
                        break
                if t:
                    pts.append({"t": t, "url": url})
            elif isinstance(p, str) and p.strip():
                pts.append({"t": p.strip(), "url": ""})
        ind["points"] = pts

    trans = {x.get("i"): x for x in d.get("items", []) if isinstance(x, dict)}
    updates = list(updates_pre)
    for i in todo:
        x = trans.get(i) or {}
        zt = (x.get("zh") or "").strip()
        zs = (x.get("zs") or "").strip()
        existing_zh = (items[i].get("zh") or "").strip()
        # 已有有效中文标题不被 LLM 输出覆盖（LLM 偶发把英文原文抄进 zh）
        if existing_zh and _norm_title(existing_zh) != _norm_title(items[i].get("title")):
            final_zh = existing_zh
        else:
            final_zh = zt
        final_zs = (items[i].get("zh_summary") or "").strip() or zs
        if final_zh:
            items[i]["zh"] = final_zh
        if final_zs:
            items[i]["zh_summary"] = final_zs
        if final_zh or final_zs:
            updates.append((_norm_title(items[i].get("title")),
                            {"zh": final_zh, "zs": final_zs}))
    ind["_zh_updates"] = updates                # run_digest 统一合并入缓存（线程安全）
    return ind


def run_digest(cfg) -> dict:
    """对 news.json 全部行业生成要点+翻译并落盘。返回 {data, failed_industries}。"""
    global ZH_CACHE
    ZH_CACHE = store.load_zh_cache()
    print("翻译缓存已有 %d 条" % len(ZH_CACHE))

    data = store.load_news()
    if not data:
        raise RuntimeError("news.json 不存在，先跑抓取（run_fetch）")
    inds = data["industries"]
    workers = _workers()
    print("对 %d 个行业生成 AI 要点+翻译(%s 通道,%d 并发)…"
          % (len(inds), "订阅" if workers == 1 else "API", workers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda i: process(i, cfg), inds))

    # 合并各栏的翻译进缓存并截尾
    for ind in inds:
        for k, v in ind.pop("_zh_updates", []):
            ZH_CACHE[k] = v
    store.save_zh_cache(ZH_CACHE)

    data["has_ai"] = True
    store.save_news(data)
    failed = []
    for ind in inds:
        n = len(ind.get("points", []))
        z = sum(1 for it in ind["items"] if it.get("zh"))
        zs = sum(1 for it in ind["items"] if it.get("zh_summary"))
        print("  %-16s 要点 %d 条 · 标题翻译 %d/%d · 摘要 %d/%d" % (
            ind["name"], n, z, len(ind["items"]), zs, len(ind["items"])))
        if ind["items"] and not n:             # 有新闻却没要点 = 该栏失败(空栏不算)
            failed.append(ind["name"])
    if failed:
        print("\n⚠️  %d 个栏目生成失败:%s" % (len(failed), "、".join(failed)))
        print("   重跑即可只补失败栏(已成功栏自动跳过)")
    return {"data": data, "failed_industries": failed}
