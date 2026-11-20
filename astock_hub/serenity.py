"""产业链深度研究维度（serenity-skill 方法论）——LLM 研究运行器。

serenity-skill 是"供给链卡点猎手"方法论，不是可直连的数据服务。本模块在 astock-hub 侧复现这条研究链路：
  收集该股行情/估值/K线/纪要/新闻 → 按 serenity 方法论（先排产业链层级/卡点，
  再排公司，证据分级，给出反方与下一步验证）喂给 LLM → 产出结构化研究报告。

LLM 走与 TradingAgents 相同的 openai_compatible 网关，密钥由用户自行配置。
输出定位：serenity 方法论 + 面板已有数据的**快速扫描版**（非 25 源全量深度调研）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import models
from .adapters.hithink import HithinkAdapter
from .adapters.investment_news import InvestmentNewsAdapter
from .adapters.zsxq import ZsxqAdapter
from .symbols import normalize

# serenity 方法论（SKILL.md 的中文蒸馏，保持"先层级后公司/证据分级/反方/下一步"骨架）
_SERENITY_SYSTEM = """你是 serenity 风格的产业链卡点研究员。针对给定的 A 股公司，做供给链瓶颈级深度研究。

工作骨架（严格遵守）：
1. 先排产业链层级/卡点，再谈公司——市场故事→系统变化→哪一层变稀缺（供给少、扩产难、认证长、材料纯度/专用设备/交期长、客户认证）。
2. 用平实中文讲清：这家公司卡住的环节、产业链位置、为什么排这里。
3. 证据分级：每个关键论点标注 强/中/弱/未证实。
4. 给出反方理由（什么情况说明判断错了）。
5. 给下一步可验证的具体动作（看什么财报指标/公告/客户/产能/订单）。

要求：先用层级开场，再公司；不要空话；不编造价格/公告/客户/市值；只基于提供的数据与你的行业理解输出，数据不足就明说"这一点需要核实"。
**公司名称、代码、主营以给定数据为准**：A 股公司常有更名/借壳/主业转型，不要用你记忆里的旧名称或旧业务来判断这家公司（记不清就按给定的名称与代码说，不要替换成你记忆中的公司）；名称缺失时直接说"公司名称未获取到"，不要猜。

【输出必须是严格 JSON（不要任何多余文字、不要用 markdown 代码块、不要多余空格换行，紧凑单行或少量换行）】，字段：
{
 "one_line": "一句话定位（这家公司卡住的环节）",
 "scarce_layers": [{"layer": "产业链层级名", "reason": "为什么是卡点/约束", "strength": "强|中|弱"}],
 "company_position": {"layer": "公司所处/最接近的层级", "role": "控制卡点|供给卡点|受益|纯故事", "why": "为什么排这里"},
 "evidence": [{"point": "证据点", "strength": "强|中|弱|未证实"}],
 "market_missing": "市场可能没看清/没充分定价的地方",
 "bear_case": "反方理由（什么情况说明判断错了）",
 "risks": ["主要风险1", "主要风险2"],
 "next_checks": ["下一步验证动作1", "下一步验证动作2"]
}"""


def _read_llm_key(cfg: dict) -> tuple[str | None, str]:
    """读 LLM key：优先 config [llm].api_key，否则解析 TradingAgents .env。"""
    key = (cfg.get("llm", {}) or {}).get("api_key")
    if key:
        return key, "config"
    env_path = (cfg.get("llm", {}) or {}).get(
        "env_file",
        str(Path(__file__).resolve().parents[1]
            / "TradingAgents-astock" / ".env"))
    try:
        for line in Path(env_path).read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENAI_COMPATIBLE_API_KEY="):
                return line.split("=", 1)[1].strip(), "tradingagents.env"
    except OSError:
        pass
    return None, "missing"


def _gather_context(cfg: dict, code: str) -> dict:
    """收集该股的研究上下文（行情/估值/纪要/新闻）。

    名称一律以数据源为准，不接受调用方传入：面板曾把股票代码当名称传进来，
    模型拿到"公司：300209（300209）"后只好凭记忆补名字，结果用了改名前的旧名。
    """
    sym = normalize(code)
    ht = HithinkAdapter(cfg.get("hithink", {}))
    zsxq = ZsxqAdapter(cfg.get("zsxq", {}))
    news = InvestmentNewsAdapter(cfg.get("investment_news", {}))
    ctx: dict = {"code": sym.bare, "name": None}

    items = ht.search(sym.bare, limit=1)
    if items and items[0].get("name"):
        ctx["name"] = items[0]["name"]

    snap = ht.snapshot([sym.ts_code])
    if snap.status == models.OK and (snap.data or {}).get("item"):
        it = snap.data["item"][0]
        ctx["price"] = it.get("last_price")
        ctx["change_pct"] = it.get("price_change_ratio_pct")

    val = ht.valuation_thscodes([sym.ts_code])
    if val.status == models.OK and (val.data or {}).get("item"):
        v = val.data["item"][0]
        ctx["pe_ttm"] = v.get("pe_ttm")
        ctx["pb"] = v.get("pb_mrq")

    keyword = ctx["name"] or sym.bare
    zx = zsxq.search(keyword, days=14)
    if zx.status == models.OK and (zx.data or {}).get("hits"):
        ctx["zsxq_hits"] = [
            {"date": h.get("report_date"), "context": (h.get("context") or "")[:280]}
            for h in zx.data["hits"][:6]]
    nw = news.search(keyword)
    if nw.status == models.OK and (nw.data or {}).get("hits"):
        ctx["news_hits"] = [
            {"source": h.get("source"), "zh": h.get("zh") or h.get("title"), "time": h.get("time")}
            for h in nw.data["hits"][:6]]
    return ctx


def _build_messages(ctx: dict) -> list[dict]:
    data_lines = [f"公司：{ctx.get('name') or '（名称未获取到）'}（{ctx.get('code')}）"]
    if ctx.get("price") is not None:
        data_lines.append(f"现价 {ctx['price']} 元，当日涨跌 {ctx.get('change_pct')}%")
    if ctx.get("pe_ttm") is not None:
        data_lines.append(f"估值：PE(TTM) {ctx['pe_ttm']}，PB {ctx.get('pb')}")
    if ctx.get("zsxq_hits"):
        data_lines.append("【付费纪要近期提及】")
        for h in ctx["zsxq_hits"]:
            data_lines.append(f"- [{h['date']}] {h['context']}")
    if ctx.get("news_hits"):
        data_lines.append("【全球产业新闻提及】")
        for h in ctx["news_hits"]:
            data_lines.append(f"- [{h.get('time')}] {h.get('zh')}")
    user = "\n".join(data_lines) + "\n\n请按 serenity 方法论，对这家公司做供给链卡点深度研究，输出严格 JSON。"
    return [
        {"role": "system", "content": _SERENITY_SYSTEM},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出里抠出第一个完整 JSON 对象（容忍代码块/前后杂讯/简单平衡括号）。

    响应被 max_tokens 截断时（推理模型的 reasoning token 也占预算），回退到
    「最后一个完整键值边界 + 补齐闭合括号」，保住已生成的字段并标 `_partial`。
    """
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        return None
    # 平衡括号找第一个完整对象（字符串内括号不计）；cut 记最后一个顶层键值边界
    depth = 0
    in_str = False
    esc = False
    cut = -1
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
            if depth == 1:
                cut = i
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start:i + 1])
                except ValueError:
                    break
        elif ch == "," and depth == 1:
            cut = i
    return _salvage_truncated(cleaned, start, cut)


def _salvage_truncated(cleaned: str, start: int, cut: int) -> dict | None:
    """截断兜底：截到最后一个完整键值边界，按未闭合层级补 `}`/`]` 后解析。"""
    if cut <= start:
        return None
    frag = cleaned[start:cut]
    stack: list[str] = []
    in_str = False
    esc = False
    for ch in frag:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    if in_str or not stack:
        return None
    closers = "".join("}" if c == "{" else "]" for c in reversed(stack))
    try:
        report = json.loads(frag + closers)
    except ValueError:
        return None
    if isinstance(report, dict):
        report["_partial"] = True
        return report
    return None


def run_serenity(cfg: dict, code: str) -> dict:
    """跑 serenity 深度研究，返回结构化报告 dict。失败抛异常。"""
    from .llm_client import call_llm, resolve_channel  # 惰性：llm_client 顶层反向依赖本模块

    llm_cfg = cfg.get("llm", {}) or {}
    timeout = int(llm_cfg.get("timeout_sec", 120))

    ctx = _gather_context(cfg, code)
    messages = _build_messages(ctx)
    ch = resolve_channel(cfg)
    # 通道统一收口 call_llm（api 走 httpx+退避，claude-cli 走本机订阅）；
    # mimo 推理 token 也计入 max_tokens——正文预算给足，否则 JSON 被截断
    text = call_llm(cfg, messages, timeout=timeout, max_tokens=16000)

    report = _extract_json(text) or {"raw": text, "one_line": "（未能解析为结构化 JSON，见 raw）"}
    return {
        "code": ctx.get("code"), "name": ctx.get("name"),
        "model": ch["model"] or ("Claude 订阅" if ch["kind"] == "claude-cli" else ""),
        "key_source": ch["source"],
        "context": {k: ctx.get(k) for k in ("price", "change_pct", "pe_ttm", "pb")},
        "report": report,
    }
