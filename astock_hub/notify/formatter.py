"""消息格式化：各类内容 → Markdown"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_FRAMEWORK_REPORT = _ROOT / "data" / "framework_report_latest.json"


def load_framework_report(as_of: str) -> dict | None:
    """读三层择时框架当日报告（scripts/framework_report.py 产出，16:00 链 3b 在
    推送前生成）。基准日不符或缺档返回 None（调用方降级并标注）。"""
    try:
        if not _FRAMEWORK_REPORT.exists():
            return None
        rep = json.loads(_FRAMEWORK_REPORT.read_text(encoding="utf-8"))
        return rep if rep.get("asof") == as_of else None
    except (OSError, ValueError):
        return None


def _triad_str(triad: dict) -> str:
    parts = []
    for name, blk in (triad or {}).items():
        key = name.split(" ", 1)[-1] if isinstance(name, str) else str(name)
        parts.append(f"{key}={blk.get('status', '—')}")
    return "；".join(parts)


def _promo_str(promo: dict) -> str:
    """晋级率渲染（兼容"样本不足"与"存档缺口跳过"两种注记）。"""
    if not promo:
        return "样本不足"
    if "_note" in promo:
        return str(promo["_note"])
    items = []
    for k, v in sorted(promo.items()):
        rate = v.get("rate")
        if rate is not None:
            items.append(f"{k} {rate * 100:.0f}%（{v.get('num')}/{v.get('den')}）")
    return "、".join(items) or "样本不足"


def _pct(x) -> str:
    return "—" if x is None else f"{x}%"


# 明日跟踪点：三信号未确认项的盯盘清单 + 当前模式的切换条件
_TRIAD_WATCH = {
    "① 相对收益反转": "相对收益：盯中证1000/沪深300 比值 MA5 与 MA20 是否交叉",
    "② 成交结构放大": "成交结构：盯第一主线占比环比是否放大（≥+0.1pp）",
    "③ 龙头行为切换": "龙头：盯主线 rank1 是否易主、昨日龙头是否断板",
}
_MODE_WATCH = {
    "防守": "模式切回条件：三信号 ≥2 个确认 或 四象限脱离双弱——在此之前只跟踪不买入",
    "短线套利": "盯昨涨停今表现：转负当日停止接力；三信号共振确认则按新风格换主线",
    "趋势交易": "盯波动率分位（升破 70 降档）与风格切换信号（三信号共振则换主线）",
}


def _watch_lines(fw: dict) -> list[str]:
    out = []
    for name, blk in (fw.get("triad") or {}).items():
        if blk.get("status") != "确认" and name in _TRIAD_WATCH:
            out.append(_TRIAD_WATCH[name])
    mode_watch = _MODE_WATCH.get(fw.get("mode") or "")
    if mode_watch:
        out.append(mode_watch)
    return out


def format_daily_review(data: dict) -> str:
    """大盘复盘 → Markdown（飞书兼容格式）。

    2026-10-06 起按三层择时框架重构（references/timing-framework.md）：
    头部 = 模式判定/四象限/三信号 → 第一层风格 → 第二层情绪（regime+五指标）
    → 指数/宽度/主线/梯队（原有）→ 操作提示（手册要点）→ 风险提示。
    不再单独计算"盘面信号分"（旧版从上涨占比硬造的第二情绪分，违反口径唯一原则）。
    """
    as_of = data.get("as_of", "")
    idx = data.get("indices", [])
    breadth = data.get("breadth", {})
    regime = data.get("regime", {})
    # build_dashboard 返回平铺结构（limit 在顶层）；"dashboard" 包装层并不存在，
    # 此前读 data["dashboard"] 恒为空 → 涨停/跌停恒 0/0、梯队段从不渲染（2026-10-10 修）
    dashboard = data.get("dashboard") or data
    concept_rank = data.get("concept_rank", {})
    fw = load_framework_report(as_of)

    lines = [f"🎯 **大盘复盘**\n"]
    lines.append(f"📅 **{as_of}**\n")

    # 段落编号动态生成：框架段（风格/情绪/操作提示）缺档时后续段落顺延，不跳空
    _sec = [0]
    _CN = "一二三四五六七八九十"

    def h(title: str) -> str:
        _sec[0] += 1
        return f"**{_CN[_sec[0] - 1]}、{title}**\n"

    total = breadth.get("total", 1) or 1
    up = breadth.get("up", 0) or 0
    down = breadth.get("down", 0) or 0
    up_pct = breadth.get("up_pct", 0) or 0
    limit_up = dashboard.get("limit", {}).get("limit_up", 0) or 0
    limit_down = dashboard.get("limit", {}).get("limit_down", 0) or 0
    score = regime.get("score", 50) or 50
    label = regime.get("label") or regime.get("state_label") or ""

    # === 头部：模式判定 / 四象限 / 三信号（框架报告为准）===
    if fw:
        lines.append(f"🧭 **模式判定：{fw.get('mode')}** ｜ {fw.get('quadrant')}"
                     f" ｜ 三信号 {fw.get('triad_confirmed')}/3（{_triad_str(fw.get('triad') or {})}）\n")
        _il = fw.get("independent_lens") or {}
        _nd = _il.get("node") or {}
        if _nd:
            _d = _nd.get("depth")
            lines.append("❄️ **独立口径·冰冰点节点**：" + str(_nd.get("state"))
                         + (f"（深度 {_d}）" if _d is not None else "")
                         + ("　→ 冰点日，冰冰点手册优先" if _il.get("ice_day") else ""))
    else:
        lines.append("**⚠️ 框架报告缺失或基准日不符**——本复盘无模式判定（先跑 16:00 链 3b）")
        lines.append(f"**盘面总览**：情绪 {score} 分（{label}）；上涨 {up} 只（{up_pct:.1f}%）/ 下跌 {down} 只\n")

    # === 第一层·风格 ===
    if fw:
        l1 = fw.get("layer1_style") or {}
        rel, vol = l1.get("relative") or {}, l1.get("volatility") or {}
        lines.append(h("第一层·风格"))
        dir_cn = {"up": "上行", "down": "下行"}.get(rel.get("direction"),
                                                     rel.get("direction") or "—")
        lines.append(f"    • 相对收益（{rel.get('pair', '小盘/大盘')}）：比值 {rel.get('ratio')}"
                     f"（MA5 {rel.get('ma5')} / MA20 {rel.get('ma20')}，方向 {dir_cn}，"
                     f"近3日交叉 {rel.get('cross_3d') or '无'}）")
        lines.append(f"    • 近5日：小盘 {_pct(rel.get('ret5_small_pct'))}"
                     f" vs 大盘 {_pct(rel.get('ret5_large_pct'))}（差 {rel.get('ret5_gap')}pp）")
        vline = f"    • 波动率：{vol.get('value')}%（分位 {vol.get('percentile')}）"
        if (vol.get("percentile") or 0) >= 70:
            vline += " ⚠️ 高波动"
        lines.append(vline)
        mc = ((l1.get("turnover") or {}).get("mainline_concepts")) or []
        if mc:
            t1 = mc[0]
            d = t1.get("delta_pp")
            ds = f"{d:+.2f}pp" if isinstance(d, (int, float)) else "—"
            lines.append(f"    • 成交结构：第一主线 {t1.get('concept')} 占比 "
                         f"{t1.get('share_pct')}%（环比 {ds}）；"
                         f"行业 top5 集中度 {(l1.get('turnover') or {}).get('industry_top5_share_pct')}%")
        lines.append("")

    # === 二、第二层·情绪（regime + 五指标分解）===
    if fw:
        e2 = fw.get("layer2_emotion") or {}
        counts = e2.get("counts") or {}
        y = e2.get("yzt") or {}
        lines.append(h(f"第二层·情绪（regime {e2.get('regime_score')} 分，"
                       f"{e2.get('state')}/{e2.get('phase')}）"))
        lines.append(f"    • 涨停 {counts.get('zt')} / 炸板 {counts.get('zb')}"
                     f"（炸板率 {counts.get('break_rate')}%）/ 跌停 {counts.get('dt')}，"
                     f"最高 {counts.get('max_height')} 连板")
        lines.append(f"    • 晋级率：{_promo_str(e2.get('promo') or {})}")
        lines.append(f"    • 昨涨停今表现：均值 {_pct(y.get('avg_pct'))} / 中位 "
                     f"{_pct(y.get('median_pct'))} / 红盘 {y.get('red_rate')}"
                     f"（{y.get('n')} 只）；高位股均值 {_pct(y.get('high_avg_pct'))}")
        # 2026-11-20 用户拍板"飞书不显示数据来源"：参考注释行（财联社/开盘红
        # 等品牌字样）与末尾"数据来源"脚注一并移除，与面板版口径一致。
        lines.append("")

    # === 指数结构 ===
    lines.append(h("指数结构"))
    for i in idx[:4]:
        pct = i.get("change_pct") or 0
        price = i.get("last_price") or 0
        emoji = "🟢" if pct >= 0 else "🔴"
        lines.append(f"    • {emoji} {i.get('name', '')}：{price:.2f}（{pct:+.2f}%）")
    lines.append("")

    # === 市场宽度 ===
    lines.append(h("市场宽度"))
    lines.append(f"    • 上涨/下跌/平盘：{up} / {down} / {breadth.get('flat', 0)}（上涨占比 {up_pct:.1f}%）")
    lines.append(f"    • 涨停/跌停：{limit_up} / {limit_down}（涨跌停差 {'+' if limit_up > limit_down else ''}{limit_up - limit_down}）")
    strong_up = breadth.get("strong_up", 0) or 0
    strong_down = breadth.get("strong_down", 0) or 0
    lines.append(f"    • 强势(>3%)/弱势(<-3%)：{strong_up} / {strong_down}（强弱比 {strong_up}:{strong_down}）\n")

    # === 板块主线 ===
    leading = concept_rank.get("leading", [])[:5]
    if leading:
        lines.append(h("板块主线"))
        lines.append("**领涨概念**：")
        for i, b in enumerate(leading, 1):
            pct = (b.get("avg_pct", 0) or 0) * 100
            leader = b.get("leader", {})
            leader_name = leader.get("name", "") if leader else ""
            lines.append(f"    {i}. {b.get('name', '')} +{pct:.2f}%（{b.get('count', 0) or 0}只）龙头：{leader_name}")
        lines.append("")

    # === 涨停梯队 ===
    ladder = dashboard.get("limit", {}).get("tiers", [])
    if ladder:
        lines.append(h("涨停梯队"))
        # 2026-10-10：显示全部连板档位（此前 [:6] 砍掉低板），count=0 缺口档跳过
        for tier in ladder:
            boards = tier.get("boards", 0) or 0
            count = tier.get("count", 0) or 0
            if count <= 0:
                continue
            names = ", ".join([s.get("name", s.get("symbol", "")) for s in tier.get("stocks", [])[:3]])
            lines.append(f"    • {boards}连板：{count}只（{names}）")
        lines.append("")

    # === 操作提示（对应模式手册要点）===
    if fw:
        lines.append(h(f"操作提示（{fw.get('mode')} 模式手册）"))
        for p in fw.get("playbook") or []:
            lines.append(f"    ▸ {p}")
        lines.append("")

    # === 风险提示 ===
    lines.append(h("风险提示"))
    if up_pct < 40:
        lines.append("    ⚠️ **市场偏弱**：上涨家数不足40%，亏钱效应显著，注意控制仓位")
    if strong_down > strong_up * 2:
        lines.append(f"    ⚠️ **弱势股偏多**：弱势 {strong_down} 只 vs 强势 {strong_up} 只，市场分化严重")
    if limit_down > 10:
        lines.append(f"    ⚠️ **跌停家数偏多**：{limit_down} 只跌停，恐慌情绪蔓延")
    if fw:
        for f in fw.get("flags") or []:
            lines.append(f"    ⚠️ {f}")
        if not fw.get("aligned", True):
            lines.append("    ⚠️ 各数据源基准日不一致，结论按低置信看待")
    # === 明日跟踪点（三信号未确认项 + 当前模式的切换条件）===
    if fw:
        watch = _watch_lines(fw)
        if watch:
            lines.append("")
            lines.append(h("明日跟踪点"))
            for w in watch:
                lines.append(f"    👀 {w}")
    return "\n".join(lines)


# TA 推送卡中文化（2026-10-22 用户反馈：缺个股名称 + 部分词汇英文）
_TA_DECISION_CN = {"Buy": "买入", "Sell": "卖出", "Hold": "持有",
                   "Overweight": "增持", "Underweight": "减持", "Neutral": "中性"}

_TA_REPORT_TITLES = {
    "market_report": "技术面", "sentiment_report": "市场情绪", "news_report": "新闻",
    "fundamentals_report": "基本面", "policy_report": "政策", "hot_money_report": "资金",
    "lockup_report": "解禁", "investment_debate": "多空辩论", "risk_judge": "风险裁决",
}

# 只译结构性标签（LLM 混入的英文小节头），不碰正文术语（MLCC/公司名等保留）
_TA_LABEL_PAIRS = (
    ("**Rating**: Hold", "**评级**：持有"),
    ("**Rating**: Buy", "**评级**：买入"),
    ("**Rating**: Sell", "**评级**：卖出"),
    ("FINAL TRANSACTION PROPOSAL", "最终交易建议"),
    ("Executive Summary", "执行摘要"),
    ("**Rating**", "**评级**"),
    ("Rating:", "评级："),
    ("Investment Thesis", "投资逻辑"),
    ("Risk Assessment", "风险评估"),
)


def _ta_en_to_cn(text: str) -> str:
    for en, cn in _TA_LABEL_PAIRS:
        text = text.replace(en, cn)
    return text


def format_ta_result(result: dict, code: str = None) -> str:
    """TA / Serenity 深度分析结果 → Markdown（飞书兼容格式）

    飞书卡片 markdown 元素不支持：标题(# ##)、引用(>)、表格(|)
    支持：加粗(**)、列表(-)、链接、代码块
    """
    name = result.get("name", "")
    decision = result.get("decision", {})
    reports = result.get("reports", {})
    meta = result.get("meta", {})
    supply_chain = result.get("supply_chain", {})
    conclusion = result.get("conclusion", "")

    # 检查是否是 Serenity 格式（有 report 字段）
    report = result.get("report")
    if isinstance(report, dict) and "one_line" in report:
        return format_serenity_result(result, code)

    lines = [f"🔬 **深度分析（{name or code or ''}）**\n"]

    # 决策结论（兼容 TA 的字符串格式和 Serenity 的字典格式）
    if decision:
        if isinstance(decision, str):
            # TA 格式：decision 是英文枚举（Hold/Buy/Sell…）→ 中文化
            final_decision = result.get("final_trade_decision", "")
            lines.append(f"**投资建议**：{_TA_DECISION_CN.get(decision, decision)}")
            if final_decision and final_decision != decision:
                lines.append(f"**最终决策**：{_ta_en_to_cn(str(final_decision))}")
            lines.append("")
        elif isinstance(decision, dict):
            # Serenity 格式：decision 是字典
            action = decision.get("action", decision.get("recommendation", ""))
            confidence = decision.get("confidence", 0)
            lines.append(f"**建议**：{action}（置信度 {confidence}）")
            if decision.get("reason"):
                lines.append(f"{decision['reason'][:200]}")
            lines.append("")

    # 产业链角色（Serenity 格式）
    if supply_chain:
        role = supply_chain.get("role", "")
        bottleneck = supply_chain.get("bottleneck", "")
        lines.append(f"**产业链角色**：{role}")
        if bottleneck:
            lines.append(f"**瓶颈判断**：{bottleneck}")
        lines.append("")

    # 核心观点（Serenity=dict-summary；TA=字符串报告 → 摘要节选）
    if reports:
        lines.append("**核心观点**")
        for name_key, report in reports.items():
            title = _TA_REPORT_TITLES.get(name_key, name_key)
            if isinstance(report, dict) and report.get("summary"):
                lines.append(f"    • **{title}**：{report['summary'][:300]}")
            elif isinstance(report, str) and report.strip():
                excerpt = _ta_en_to_cn(" ".join(report.split()))[:220]
                lines.append(f"    • **{title}**：{excerpt}…")
        lines.append("")

    # 结论（Serenity 格式）
    if conclusion:
        lines.append(f"**结论**：{conclusion[:300]}")
        lines.append("")

    # 风险提示
    if meta.get("risks"):
        lines.append("**风险提示**")
        for risk in meta["risks"][:3]:
            lines.append(f"    • {risk}")
        lines.append("")

    lines.append(f"分析时间：{meta.get('timestamp', '')}")
    return "\n".join(lines)


def format_serenity_result(result: dict, code: str = None) -> str:
    """Serenity 产业链研究结果 → Markdown（飞书兼容格式）

    处理 serenity.py 返回的结构：{code, name, report: {one_line, scarce_layers, ...}}
    """
    name = result.get("name", "")
    report = result.get("report", {})
    context = result.get("context", {})

    lines = [f"🔬 **产业链研究（{name or code or ''}）**\n"]

    # 一句话定位
    one_line = report.get("one_line", "")
    if one_line:
        lines.append(f"**定位**：{one_line}\n")

    # 产业链卡点层级
    scarce_layers = report.get("scarce_layers", [])
    if scarce_layers:
        lines.append("**产业链卡点层级**")
        for layer in scarce_layers[:3]:
            layer_name = layer.get("layer", "")
            reason = layer.get("reason", "")
            strength = layer.get("strength", "")
            lines.append(f"    • **{layer_name}**（{strength}）：{reason}")
        lines.append("")

    # 公司位置
    company_pos = report.get("company_position", {})
    if company_pos:
        role = company_pos.get("role", "")
        why = company_pos.get("why", "")
        lines.append(f"**公司角色**：{role}")
        if why:
            lines.append(f"**为什么排这里**：{why}")
        lines.append("")

    # 关键证据
    evidence = report.get("evidence", [])
    if evidence:
        lines.append("**关键证据**")
        for ev in evidence[:3]:
            point = ev.get("point", "")
            strength = ev.get("strength", "")
            lines.append(f"    • [{strength}] {point}")
        lines.append("")

    # 市场可能没看清的地方
    market_missing = report.get("market_missing", "")
    if market_missing:
        lines.append(f"**预期差**：{market_missing}\n")

    # 反方理由
    bear_case = report.get("bear_case", "")
    if bear_case:
        lines.append(f"**反方理由**：{bear_case}\n")

    # 风险
    risks = report.get("risks", [])
    if risks:
        lines.append("**风险提示**")
        for risk in risks[:3]:
            lines.append(f"    • {risk}")
        lines.append("")

    # 下一步验证
    next_checks = report.get("next_checks", [])
    if next_checks:
        lines.append("**下一步验证**")
        for check in next_checks[:3]:
            lines.append(f"    • {check}")
        lines.append("")

    # 行情快照（如有）
    if context:
        lines.append("**行情快照**")
        price = context.get("price", "")
        change = context.get("change_pct", "")
        pe = context.get("pe_ttm", "")
        if price:
            lines.append(f"    • 现价：{price}（{change:+.2f}%）")
        if pe:
            lines.append(f"    • PE(TTM)：{pe}")
        lines.append("")

    lines.append(f"模型：{result.get('model', '')} | 数据源：{result.get('key_source', '')}")
    return "\n".join(lines)


def format_alert_trigger(rule: dict, message: str) -> str:
    """告警触发 → Markdown（飞书兼容格式）"""
    symbol = rule.get("symbol", "")
    name = rule.get("name", "")
    alert_type = rule.get("type", "")
    threshold = rule.get("threshold")

    lines = [f"⚠️ **告警触发：{name} ({symbol})**\n"]
    lines.append(f"**规则类型**：{alert_type}")
    if threshold is not None:
        lines.append(f"**阈值**：{threshold}")
    lines.append(f"**当前**：{message}")
    return "\n".join(lines)


def format_screen_result(result: dict) -> str:
    """策略选股结果 → Markdown（飞书兼容格式）"""
    strategy = result.get("strategy", "")
    hits = result.get("hits", [])
    as_of = result.get("as_of", "")

    lines = [f"📋 **策略信号：{strategy}（{as_of}）**\n"]

    if not hits:
        lines.append("**无命中**")
    else:
        lines.append(f"**命中 {len(hits)} 只**：")
        for h in hits[:5]:
            symbol = h.get("symbol", "")
            name = h.get("name", "")
            change = h.get("change_pct", 0) or 0
            emoji = "📈" if change >= 0 else "📉"
            lines.append(f"    • {emoji} {name} ({symbol}) {change:+.2f}%")
        if len(hits) > 5:
            lines.append(f"    • ... 共 {len(hits)} 只")

    return "\n".join(lines)
