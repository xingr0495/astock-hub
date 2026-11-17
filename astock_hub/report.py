"""把 overview Envelope 渲染成单文件 HTML 报告（人看入口）。

无外部依赖、无 JS，内联 CSS；所有动态内容都做 HTML 转义。
"""

from __future__ import annotations

import html

STATUS_LABEL = {
    "ok": ("正常", "#1a7f37"), "stale": ("偏旧", "#9a6700"),
    "partial": ("部分可用", "#9a6700"), "not_found": ("无数据", "#57606a"),
    "unavailable": ("不可用", "#cf222e"), "skipped": ("跳过", "#57606a"),
    "not_applicable": ("不适用", "#57606a"),
}

_CSS = """
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 0;
       background: #f6f8fa; color: #1f2328; }
.wrap { max-width: 960px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; } h2 { font-size: 17px; border-left: 4px solid #0969da;
       padding-left: 8px; margin-top: 28px; }
.card { background: #fff; border: 1px solid #d0d7de; border-radius: 8px;
        padding: 16px; margin-top: 12px; }
.badge { display: inline-block; color: #fff; border-radius: 10px;
         padding: 2px 10px; font-size: 12px; margin-left: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }
th { background: #f6f8fa; }
pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto;
      white-space: pre-wrap; word-break: break-all; font-size: 13px; }
.muted { color: #57606a; font-size: 13px; }
.metric { display: inline-block; margin: 4px 16px 4px 0; }
.metric b { font-size: 20px; }
"""


def _e(text) -> str:
    return html.escape(str(text)) if text is not None else ""


def _badge(status: str) -> str:
    label, color = STATUS_LABEL.get(status, (status, "#57606a"))
    return f'<span class="badge" style="background:{color}">{_e(label)}</span>'


def _kv_table(rows: list[tuple[str, object]]) -> str:
    body = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows)
    return f"<table>{body}</table>"


def _dim_section(title: str, dim: dict, inner: str) -> str:
    parts = [f"<h2>{_e(title)}{_badge(dim.get('status', ''))}</h2>", '<div class="card">']
    as_of = (dim.get("meta") or {}).get("as_of")
    if as_of:
        parts.append(f'<p class="muted">数据截止：{_e(as_of)}</p>')
    err = dim.get("error")
    if err and dim.get("status") == "unavailable":
        parts.append(f'<p style="color:#cf222e">原因：{_e(err.get("message"))}</p>')
        if err.get("hint"):
            parts.append(f'<p class="muted">处理：{_e(err["hint"])}</p>')
    parts.append(inner)
    parts.append("</div>")
    return "\n".join(parts)


def _market_section(dim: dict) -> str:
    data = dim.get("data") or {}
    rows = []
    if data.get("limit_up_pool_count") is not None:
        rows.append(("今日涨停家数", data["limit_up_pool_count"]))
    if data.get("limit_down_pool_count") is not None:
        rows.append(("今日跌停家数", data["limit_down_pool_count"]))
    inner = ""
    if rows:
        inner += "".join(
            f'<span class="metric">{_e(k)} <b>{_e(v)}</b></span>' for k, v in rows)
    ladder = (data.get("hithink_limit_ladder") or {}).get("data")
    if ladder:
        items = ladder.get("item") or ladder.get("ladder") or ladder
        inner += f"<h3>连板梯队</h3><pre>{_e(_short_json(items))}</pre>"
    return _dim_section("📈 市场行情", dim, inner)


def _short_json(obj, limit: int = 1500) -> str:
    import json
    text = json.dumps(obj, ensure_ascii=False, indent=1, default=str)
    return text[:limit] + (" …（截断）" if len(text) > limit else "")


def _nt_section(dim: dict) -> str:
    data = dim.get("data") or {}
    inner = ""
    etf = (data.get("etf_shares") or {}).get("data") or {}
    latest = etf.get("latest") or {}
    if latest:
        rows = [("数据日期", latest.get("date")),
                ("全宽基合计（亿份）", latest.get("全宽基合计")),
                ("周环比变化（亿份）", etf.get("week_over_week_total"))]
        for k in ("上证50", "沪深300", "中证500", "中证1000", "科创50", "中证A500"):
            if k in latest:
                rows.append((k, latest[k]))
        inner += "<h3>汇金宽基 ETF 份额（周频温度计）</h3>" + _kv_table(rows)
    changes = ((data.get("recent_changes") or {}).get("data") or {})
    if changes.get("changes"):
        rows = [("披露期", changes.get("period_end"))]
        inner += "<h3>最近披露期加减仓分布（前 10）</h3>"
        inner += _kv_table(rows)
        items = changes["changes"][:10]
        inner += "<table><tr><th>机构</th><th>动向</th><th>只数</th></tr>" + "".join(
            f"<tr><td>{_e(c.get('holder_class') or c.get('holder_name'))}</td>"
            f"<td>{_e(c.get('change_analysis'))}</td><td>{_e(c.get('cnt'))}</td></tr>"
            for c in items) + "</table>"
    return _dim_section("💰 国家队资金", dim, inner)


def _zsxq_section(dim: dict) -> str:
    """信息面：zsxq 纪要 + investment-news 全球产业要点。"""
    data = dim.get("data") or {}
    inner = ""
    zx = data.get("zsxq_latest") or data  # 兼容新旧结构
    sub = zx.get("data") or zx
    if sub.get("path"):
        inner += f'<p class="muted">报告：{_e(sub["path"])}</p>'
    if sub.get("excerpt"):
        inner += f"<h3>zsxq 纪要摘录</h3><pre>{_e(sub['excerpt'])}</pre>"
    news = (data.get("news") or {}).get("data") or {}
    inds = news.get("industries") or []
    if inds:
        rows = []
        for ind in inds:
            pts = "；".join((p.get("t") or "") for p in (ind.get("points") or [])[:3])
            if pts:
                rows.append((ind.get("name") or ind.get("key"), pts))
        if rows:
            body = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows)
            inner += '<h3>investment-news 全球产业要点（12 赛道）</h3>' \
                     f"<table>{body}</table>"
        elif news.get("generated_at"):
            inner += (f'<p class="muted">investment-news（{_e(news["generated_at"])}）'
                      "新闻已抓取，AI 要点层尚未生成</p>")
    return _dim_section("📡 信息面（zsxq + investment-news）", dim, inner)


def render_overview_html(envelope: dict) -> str:
    """overview Envelope（dict）→ 完整 HTML 字符串。"""
    dims = envelope.get("dimensions") or {}
    sections = []
    if "market" in dims:
        sections.append(_market_section(dims["market"]))
    if "national_team" in dims:
        sections.append(_nt_section(dims["national_team"]))
    if "zsxq" in dims:
        sections.append(_zsxq_section(dims["zsxq"]))
    summary = envelope.get("summary") or {}
    summary_text = "，".join(f"{STATUS_LABEL.get(k, (k, ''))[0]} {v}" for k, v in summary.items())
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A 股五维总览 {_e(envelope.get('generated_at', ''))[:10]}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>A 股五维总览</h1>
<p class="muted">生成时间：{_e(envelope.get("generated_at"))}　|　{_e(summary_text)}</p>
{''.join(sections)}
<p class="muted">数据来源：astock-hub 聚合层（a-stock-data / hithink / 国家队 / zsxq）。
本报告仅供研究参考，不构成投资建议。</p>
</div></body></html>"""
