"""大盘复盘 API — AI 流式复盘 + 报告持久化。

路由前缀: /api/review

端点:
  POST /analyze                AI 流式大盘复盘(NDJSON)
  GET  /reports                历史复盘列表
  POST /reports                保存一条复盘报告
  DELETE /reports/{report_id}  删除一条复盘报告
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from astock_hub.market.dashboard import build_dashboard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])

# 复盘报告存储目录（锚定包根目录，不依赖进程 cwd）
REPORTS_DIR = Path(__file__).resolve().parents[1] / "data" / "review_reports"

# review-mode 落盘目录（Closing Review 下期对照读取这里；与个股复盘 stocks/ 并列）
REVIEWS_MARKET_DIR = Path(__file__).resolve().parents[1] / "data" / "reviews" / "market"


def write_market_review_md(as_of: str, content: str, summary: str = "",
                           emotion: str = "", score=None) -> str | None:
    """把大盘复盘写成 markdown 存入 reviews/market/YYYY-MM-DD.md（尽力而为）。"""
    try:
        REVIEWS_MARKET_DIR.mkdir(parents=True, exist_ok=True)
        lines = [f"# 大盘复盘（{as_of}）", ""]
        meta = []
        if emotion:
            meta.append(f"情绪：{emotion}" + (f"（{score}分）" if score is not None else ""))
        if summary:
            meta.append(summary)
        if meta:
            lines += ["> " + " · ".join(meta), "", "---", ""]
        (REVIEWS_MARKET_DIR / f"{as_of}.md").write_text(
            "\n".join(lines) + content, encoding="utf-8")
        return str(REVIEWS_MARKET_DIR / f"{as_of}.md")
    except OSError:
        return None


class AnalyzeRequest(BaseModel):
    """AI 大盘复盘请求。"""
    as_of: str | None = None  # 可选:复盘日期(YYYY-MM-DD),缺省取最新有数据日
    focus: str = ""           # 可选:用户追加的复盘关注点


class SaveReportRequest(BaseModel):
    """保存一条复盘报告。"""
    as_of: str
    focus: str = ""
    content: str
    summary: str = ""
    emotion_score: int | None = None
    emotion_label: str = ""


def _ensure_reports_dir():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _build_review_prompt(data: dict, focus: str = "") -> str:
    """构建复盘提示词。"""
    as_of = data.get("as_of", "")
    indices = data.get("indices", [])
    breadth = data.get("breadth", {})
    limit = data.get("limit", {})
    emotion = data.get("emotion", {})
    radar = data.get("radar", [])
    concept_rank = data.get("concept_rank", {})
    industry_rank = data.get("industry_rank", {})

    # 指数信息（历史日个别指数可能无本地数据，last_price/change_pct 会是 None，需兜底）
    idx_lines = []
    for ix in indices[:4]:
        pct = ix.get("change_pct") or 0
        price = ix.get("last_price") or 0
        emoji = "🟢" if pct >= 0 else "🔴"
        idx_lines.append(f"- {ix.get('name', '')}：{price:.2f}（{emoji} {pct:+.2f}%）")

    # 涨跌停信息
    limit_up = limit.get("limit_up", 0)
    limit_down = limit.get("limit_down", 0)
    broken = limit.get("broken", 0)
    seal_rate = limit.get("seal_rate", 0)
    max_boards = limit.get("max_boards", 0)
    tiers = limit.get("tiers", [])
    tier_lines = []
    for t in tiers:  # 2026-10-10：全部档位，count=0 缺口档不显示
        boards = t.get("boards", 0)
        count = t.get("count", 0)
        if count <= 0:
            continue
        stocks = ", ".join([s.get("name", "") for s in t.get("stocks", [])[:3]])
        tier_lines.append(f"- {boards}连板：{count}只（{stocks}）")

    # 板块主线
    leading_concepts = concept_rank.get("leading", [])[:5]
    concept_lines = []
    for c in leading_concepts:
        pct = (c.get("avg_pct", 0) or 0) * 100
        concept_lines.append(f"- {c.get('name', '')}：+{pct:.2f}%（{c.get('count', 0)}只）")

    # 情绪雷达
    radar_lines = []
    for r in radar:
        radar_lines.append(f"- {r.get('label', '')}：{r.get('value', 0)}分")

    prompt = f"""你是一个专业的 A 股市场分析师。请根据以下数据，写一份简洁的每日大盘复盘报告。

## 日期
{as_of}

## 指数行情
{chr(10).join(idx_lines) if idx_lines else "暂无数据"}

## 市场宽度
- 上涨：{breadth.get('up', 0)}只（{breadth.get('up_pct', 0):.1f}%）
- 下跌：{breadth.get('down', 0)}只（{breadth.get('down_pct', 0):.1f}%）
- 平均涨跌幅：{breadth.get('avg_pct', 0)*100:.2f}%

## 涨跌停情况
- 涨停：{limit_up}只（封板率{seal_rate:.1f}%）
- 跌停：{limit_down}只
- 炸板：{broken}只
- 最高连板：{max_boards}板

## 连板梯队
{chr(10).join(tier_lines) if tier_lines else "暂无连板"}

## 板块主线（概念涨幅前5）
{chr(10).join(concept_lines) if concept_lines else "暂无数据"}

## 情绪雷达
{chr(10).join(radar_lines)}
- 综合评分：{emotion.get('score', 50)}分（{emotion.get('label', '未知')}）

## 复盘要求
请按以下结构输出复盘报告（使用 Markdown 格式）：

### 一、盘面总览
简述今日市场整体表现，包括涨跌家数、情绪评分。

### 二、指数结构
分析四大指数的表现，指出分化情况。

### 三、市场宽度
分析涨跌分布、强弱分化。

### 四、板块主线
分析领涨板块和领跌板块，指出主线方向。

### 五、资金与情绪
结合情绪雷达各维度，分析资金动向和市场情绪。

### 六、涨停梯队
分析连板高度和梯队结构，指出赚钱效应。

### 七、风险提示
指出当前市场的主要风险和注意事项。

### 八、明日展望
给出对明日市场的初步判断和关注点。
"""
    if focus:
        prompt += f"\n## 用户特别关注\n{focus}\n"

    return prompt


def _generate_review_content(data: dict, focus: str = "") -> tuple[str, str]:
    """生成复盘内容（同步版本，使用模板）。

    2026-10-06 起按三层择时框架重构：头部=模式判定/四象限/三信号（读
    framework_report_latest.json）→ 第一层风格 → 第二层情绪（regime+五指标）
    → 指数/宽度/主线/梯队（原有）→ 操作提示 → 风险提示。
    返回 (content, summary)
    """
    from .notify.formatter import load_framework_report, _promo_str, _triad_str, _watch_lines

    as_of = data.get("as_of", "")
    indices = data.get("indices", [])
    breadth = data.get("breadth", {})
    limit = data.get("limit", {})
    emotion = data.get("emotion", {})
    radar = data.get("radar", [])
    concept_rank = data.get("concept_rank", {})
    industry_rank = data.get("industry_rank", {})
    trend = data.get("trend", {})
    activity = data.get("activity", {})
    fw = load_framework_report(as_of)

    # 指数信息（历史日个别指数可能无本地数据，last_price/change_pct 会是 None，需兜底）
    idx_lines = []
    for ix in indices[:4]:
        pct = ix.get("change_pct") or 0
        price = ix.get("last_price") or 0
        emoji = "🟢" if pct >= 0 else "🔴"
        idx_lines.append(f"| {ix.get('name', '')} | {price:.2f} | {emoji} {pct:+.2f}% |")

    # 涨跌停
    limit_up = limit.get("limit_up", 0)
    limit_down = limit.get("limit_down", 0)
    broken = limit.get("broken", 0)
    seal_rate = limit.get("seal_rate", 0)
    max_boards = limit.get("max_boards", 0)
    tiers = limit.get("tiers", [])
    tier_lines = []
    for t in tiers:  # 2026-10-10：全部档位，count=0 缺口档不显示
        boards = t.get("boards", 0)
        count = t.get("count", 0)
        if count <= 0:
            continue
        stocks = ", ".join([s.get("name", "") for s in t.get("stocks", [])[:3]])
        tier_lines.append(f"| {boards}连板 | {count}只 | {stocks} |")

    # 板块主线
    leading_concepts = concept_rank.get("leading", [])[:5]
    concept_lines = []
    for c in leading_concepts:
        pct = (c.get("avg_pct", 0) or 0) * 100
        concept_lines.append(f"| {c.get('name', '')} | +{pct:.2f}% | {c.get('count', 0)}只 | {c.get('leader', {}).get('name', '')} |")

    # 框架块（一/二/七 段；缺档时降级并标注）
    fw_block = ""
    if fw:
        l1 = fw.get("layer1_style") or {}
        rel, vol = l1.get("relative") or {}, l1.get("volatility") or {}
        e2 = fw.get("layer2_emotion") or {}
        counts = e2.get("counts") or {}
        y = e2.get("yzt") or {}
        dir_cn = {"up": "上行", "down": "下行"}.get(rel.get("direction"),
                                                     rel.get("direction") or "—")
        mc = ((l1.get("turnover") or {}).get("mainline_concepts")) or []
        to_line = ""
        if mc:
            t1 = mc[0]
            d = t1.get("delta_pp")
            ds = f"{d:+.2f}pp" if isinstance(d, (int, float)) else "—"
            to_line = (f"\n| 成交结构 | 第一主线 {t1.get('concept')} 占比 "
                       f"{t1.get('share_pct')}%（环比 {ds}），行业top5集中度 "
                       f"{(l1.get('turnover') or {}).get('industry_top5_share_pct')}% |")
        promo_txt = _promo_str(e2.get("promo") or {})
        y_line = "—"
        if y.get("avg_pct") is not None:
            y_line = (f"均值 {y.get('avg_pct')}% / 中位 {y.get('median_pct')}%"
                      f" / 红盘 {y.get('red_rate')}；高位股均值 {y.get('high_avg_pct')}%")
        playbook = "\n".join(f"- {p}" for p in fw.get("playbook") or [])
        fw_block = f"""### 一、模式判定 🧭（三层择时框架，v1 规则）

**模式：{fw.get('mode')}** ｜ 四象限：{fw.get('quadrant')} ｜ 三信号：确认 {fw.get('triad_confirmed')}/3（{_triad_str(fw.get('triad') or {})}）

**第一层·风格**：

| 指标 | 读数 |
|------|------|
| 相对收益（{rel.get('pair', '小盘/大盘')}） | 比值 {rel.get('ratio')}，MA5 {rel.get('ma5')} / MA20 {rel.get('ma20')}（方向 {dir_cn}，近3日交叉 {rel.get('cross_3d') or '无'}） |
| 近5日大小盘 | 小盘 {rel.get('ret5_small_pct')}% vs 大盘 {rel.get('ret5_large_pct')}%（差 {rel.get('ret5_gap')}pp） |
| 波动率 | {vol.get('value')}%（分位 {vol.get('percentile')}） |{to_line}

**第二层·情绪**（regime {e2.get('regime_score')} 分 {e2.get('state')}/{e2.get('phase')}，五指标分解）：

| 指标 | 读数 |
|------|------|
| 涨停/炸板/跌停 | {counts.get('zt')} / {counts.get('zb')}（炸板率 {counts.get('break_rate')}%）/ {counts.get('dt')}，最高 {counts.get('max_height')} 连板 |
| 晋级率 | {promo_txt} |
| 昨涨停今表现 | {y_line} |

"""
    else:
        fw_block = ("### 一、模式判定\n\n"
                    "⚠️ 框架报告缺失或基准日不符——本次复盘不含模式判定"
                    "（先跑 16:00 链 3b）\n\n")

    # 生成报告
    content = f"""# 🎯 大盘复盘

## {as_of} 大盘复盘

{fw_block}### 二、指数结构

| 指数 | 最新价 | 涨跌幅 |
|------|--------|--------|
{chr(10).join(idx_lines) if idx_lines else "| 暂无数据 | - | - |"}

### 三、市场宽度

| 指标 | 数值 |
|------|------|
| 上涨/下跌/平盘 | {breadth.get('up', 0)} / {breadth.get('down', 0)} / {breadth.get('flat', 0)} |
| 涨停/跌停 | {limit_up} / {limit_down} |
| 强势(>3%)/弱势(<-3%) | {breadth.get('strong_up', 0)} / {breadth.get('strong_down', 0)} |
| 站上MA20 | {trend.get('above_ma20', 0)}只（{trend.get('above_ma20_pct', 0):.1f}%） |

### 四、板块主线

**领涨概念**：

| 板块 | 涨幅 | 成分股 | 领涨股 |
|------|------|--------|--------|
{chr(10).join(concept_lines) if concept_lines else "| 暂无数据 | - | - | - |"}

### 五、情绪雷达

| 维度 | 分数 |
|------|------|
{chr(10).join(f"| {r.get('label', '')} | {r.get('value', 0)} |" for r in radar) if radar else "| 暂无数据 | - |"}

**量能**：平均量比 {activity.get('vol_ratio') or 0:.2f}，高量比占比 {activity.get('high_vol_ratio') or 0:.1f}%

### 六、涨停梯队

| 连板数 | 数量 | 代表股 |
|--------|------|--------|
{chr(10).join(tier_lines) if tier_lines else "| 暂无连板 | - | - |"}

**封板率**：{seal_rate:.1f}%
**最高连板**：{max_boards}板

### 七、操作提示

"""
    if fw:
        for p in fw.get("playbook") or []:
            content += f"- {p}\n"
    else:
        content += "- （无模式判定，按观望处理）\n"

    content += "\n### 八、风险提示\n\n"
    # 根据情绪添加风险提示
    if emotion.get('score', 50) < 40:
        content += "- ⚠️ **市场偏弱**：情绪评分较低，注意控制仓位\n"
    if breadth.get('down_pct', 0) > 60:
        content += "- ⚠️ **下跌家数偏多**：下跌占比超过60%，亏钱效应显著\n"
    if limit_down > 10:
        content += f"- ⚠️ **跌停家数偏多**：{limit_down}只跌停，恐慌情绪蔓延\n"
    if seal_rate < 50:
        content += f"- ⚠️ **封板率偏低**：{seal_rate:.1f}%，涨停封板意愿不强\n"
    if fw:
        for f in fw.get("flags") or []:
            content += f"- ⚠️ {f}\n"
        if not fw.get("aligned", True):
            content += "- ⚠️ 各数据源基准日不一致，结论按低置信看待\n"
        watch = _watch_lines(fw)
        if watch:
            content += "\n### 九、明日关注\n\n"
            for w in watch:
                content += f"- 👀 {w}\n"
    content += "\n"

    # 生成摘要
    mode_txt = f"，模式：{fw.get('mode')}" if fw else ""
    summary = f"{as_of} 大盘复盘：情绪{emotion.get('label', '未知')}（{emotion.get('score', 50)}分）{mode_txt}，"
    summary += f"涨停{limit_up}只/跌停{limit_down}只，"
    if leading_concepts:
        summary += f"领涨板块：{leading_concepts[0].get('name', '')}，"
    summary += f"最高{max_boards}连板"

    return content, summary


def review_build_args(as_of: str | None) -> tuple[str | None, bool | None]:
    """复盘端点的取数参数（as_of, realtime_pref）。

    复盘只基于**已闭环**交易日：
    - 显式日期 → post 锁定该日；
    - 不传日期 + 盘后 → auto（今日链落库即取今日）；
    - 不传日期 + **盘中** → 强制 post（as_of=None 落到 db_max_date=最近闭环日）。
      auto 在盘中会返回今日未收盘的实时帧，而前端下拉第一项显示的是
      "最新交易日(db_max_date)"——用户选的是 22 号、拿到的却是 23 号实时数据，
      这就是「生成 22 号复盘变成 23 号」的根因（2026-10-23 用户实测）。
    """
    if as_of is not None:
        return as_of, False
    from .market.sessions import market_session

    _, is_trading = market_session()
    return None, (None if not is_trading else False)


@router.post("/analyze")
async def analyze_market(request: Request, req: AnalyzeRequest):
    """AI 大盘复盘 — 返回复盘报告并自动发送到飞书。"""
    import asyncio

    cfg = request.app.state.cfg if hasattr(request.app.state, "cfg") and request.app.state.cfg else None
    if not cfg:
        # app.state.cfg 未初始化时必须加载真实配置——空 dict 会让数据缓存路径
        # 退化成 cwd 相对的默认值（从非项目根启动时全市场数据全部 miss、回退到
        # 通达信盘后包逐日 HTTP 同步，表现为接口"永久"卡住）
        from .config import load_config
        cfg = load_config()

    # 复盘只取闭环数据（盘中不传日期 → 最近闭环日，见 review_build_args）。
    # 放到线程里跑并加超时：行情源任一侧卡死都不能拖死事件循环。
    build_as_of, realtime_pref = review_build_args(req.as_of)

    async def _build() -> dict:
        def _sync() -> dict:
            try:
                return build_dashboard(cfg, build_as_of, realtime=realtime_pref)
            except Exception:
                # realtime 失败时回退到 post 模式
                return build_dashboard(cfg, build_as_of, realtime=False)
        return await asyncio.to_thread(_sync)

    try:
        dashboard_data = await asyncio.wait_for(_build(), timeout=90)
    except asyncio.TimeoutError:
        return {"ok": False, "as_of": req.as_of,
                "error": "行情数据获取超时（90 秒）——实时行情源可能繁忙，请稍后重试或改选其他日期"}
    except Exception as e:
        return {"ok": False, "as_of": req.as_of,
                "error": f"该日期无可用行情数据（非交易日或超出本地库范围）：{e}"}

    # 生成复盘内容
    content, summary = _generate_review_content(dashboard_data, req.focus)

    # 获取情绪信息
    emotion = dashboard_data.get("emotion", {})

    # 双写：同时存入 review-mode 落盘目录（Closing Review 对照读取处）
    write_market_review_md(
        dashboard_data.get("as_of", ""), content, summary,
        emotion.get("label", ""), emotion.get("score"))

    # 自动发送到飞书/企微（仅盘后"今日复盘"；历史日期补生成、盘中回落最近闭环日
    # 都不打扰推送渠道——盘中回落时 as_of 是昨天，req.as_of 虽空也不能推）
    meta_d = dashboard_data.get("meta", {})
    today_s = date.today().isoformat()
    is_today_post = meta_d.get("mode") == "post" and dashboard_data.get("as_of") == today_s
    if not req.as_of and is_today_post:
        try:
            from astock_hub.notify.dispatcher import get_dispatcher
            dispatcher = get_dispatcher()
            if dispatcher.targets:
                dispatch_result = dispatcher.dispatch(
                    content,
                    f"大盘复盘 {dashboard_data.get('as_of', '')}（{emotion.get('label', '')}）",
                    "review"
                )
                # 记录推送结果
                import logging
                logger = logging.getLogger(__name__)
                for r in dispatch_result:
                    if r.get("ok"):
                        logger.info("复盘报告已发送到 %s", r.get("url", "")[:50])
                    else:
                        logger.warning("复盘报告发送失败 %s: %s", r.get("url", "")[:50], r)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("复盘报告推送失败: %s", e)

    return {
        "ok": True,
        "as_of": dashboard_data.get("as_of", ""),
        "content": content,
        "summary": summary,
        "emotion_score": emotion.get("score"),
        "emotion_label": emotion.get("label", ""),
    }


@router.get("/daily-status")
def daily_status():
    """最新复盘状态（reviews/market 最新 md + 推送标记）——看板状态条用。"""
    marker = Path(__file__).resolve().parents[1] / "data" / "review_pushed.json"
    pushed_dates: list[str] = []
    if marker.exists():
        try:
            pushed_dates = json.loads(marker.read_text(encoding="utf-8")).get("dates", [])
        except (OSError, ValueError):
            pushed_dates = []

    files = sorted(REVIEWS_MARKET_DIR.glob("*.md"), reverse=True) if REVIEWS_MARKET_DIR.exists() else []
    if not files:
        return {"latest": None}
    f = files[0]
    meta = ""
    try:
        for line in f.read_text(encoding="utf-8").splitlines()[:6]:
            if line.startswith("> "):
                meta = line[2:].strip()
                break
    except OSError:
        pass
    return {"latest": {"as_of": f.stem, "meta": meta,
                       "pushed": f.stem in pushed_dates, "path": str(f)}}


@router.get("/reports")
def list_reports():
    """获取全部历史复盘（按时间降序）。"""
    _ensure_reports_dir()
    reports = []
    for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["id"] = f.stem
            reports.append(data)
        except Exception:
            continue
    return {"reports": reports}


@router.post("/reports")
def save_report(req: SaveReportRequest):
    """保存一条复盘报告。"""
    _ensure_reports_dir()
    report_id = f"review_{req.as_of.replace('-', '')}"
    report = {
        "as_of": req.as_of,
        "focus": req.focus,
        "content": req.content,
        "summary": req.summary,
        "emotion_score": req.emotion_score,
        "emotion_label": req.emotion_label,
    }
    (REPORTS_DIR / f"{report_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    # 双写：同时存 review-mode 落盘目录
    write_market_review_md(req.as_of, req.content, req.summary,
                           req.emotion_label, req.emotion_score)
    return {"ok": True, "report": report}


@router.delete("/reports/{report_id}")
def delete_report(report_id: str):
    """删除一条复盘报告。"""
    _ensure_reports_dir()
    f = REPORTS_DIR / f"{report_id}.json"
    if f.exists():
        f.unlink()
        return {"ok": True}
    return {"ok": False, "error": "报告不存在"}
