"""看板 AI 对话框：意图识别与上下文组装。

设计原则（计划第四节）：**上下文确定性注入，不做 function calling**——
先从用户消息里确定性识别"问到哪些股票、问什么主题"（本文件 parse_intent），
再由 build_context 决定注入哪些数据块（本文件 T5 落地），
端点据此组装溯源 used_blocks（T6）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import resolve_path
from ..pool import _norm, md_symbol_names
from ..symbols import is_a_share_stock, normalize

# 6 位代码扫描。不用 \b：中文与数字相邻时（"买600519"）两边都是 \w、没有边界，
# 会漏匹配；改用前后非数字断言，同时避免从 8 位长数字（日期 20261013）里截出 6 位。
# 只能在**保留分隔符**的文本上扫：若先去空白（_norm），"600519 300750" 会拼成
# 18 位连续数字，一条都匹配不上。
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# 名称命中占位符（等长挖除，避免删除后左右文字拼接出新的误命中）
_BLANK = "\x00"

# 主题关键词表：关键词 → 主题 key。模块级常量，T5（决定注入哪些数据块）、
# T6（system prompt / 溯源文案）、T7（断言覆盖）直接引用。
# 匹配规则：消息 NFKC 归一后做大小写不敏感子串判断；一条消息可命中多个关键词，
# topics 为去重后的主题列表，顺序 = 本表首次出现顺序（确定性，便于断言与溯源）。
TOPIC_KEYWORDS: dict[str, str] = {
    # 模式（三层择时：趋势交易 / 短线套利 / 防守）
    # ——出手类问法（能出手/可以买吗/该不该进攻）归"模式"：手册口径由模式判定
    "模式": "mode",
    "判定": "mode",
    "择时": "mode",
    "闸门": "mode",
    "趋势交易": "mode",
    "短线套利": "mode",
    "套利": "mode",
    "防守": "mode",
    "进攻": "mode",
    "出手": "mode",
    "能买": "mode",
    "可以买": "mode",
    "能不能买": "mode",
    "该不该": "mode",
    "买吗": "mode",
    "开仓": "mode",
    "建仓": "mode",
    "仓位": "mode",
    # 四象限（指数 × 情绪）
    "四象限": "quadrant",
    "象限": "quadrant",
    # 主线 / 板块轮动
    "主线": "mainline",
    "板块": "mainline",
    "轮动": "mainline",
    "龙头": "mainline",
    # 涨跌停
    "涨停": "limit_up",
    "跌停": "limit_up",
    "炸板": "limit_up",
    # 连板梯队
    "梯队": "ladder",
    "连板": "ladder",
    "天梯": "ladder",
    "晋级": "ladder",
    # 复盘 / 大盘回顾
    "复盘": "review",
    "盘面": "review",
    "大盘": "review",
    "今天怎么样": "review",
    # 情绪（情绪分 / 赚钱效应 / 周期阶段）
    "情绪": "sentiment",
    "赚钱效应": "sentiment",
    "冰点": "sentiment",
    "退潮": "sentiment",
    "高潮": "sentiment",
    # 就绪度（趋势/套利手册就绪 + RPS）
    "就绪": "readiness",
    "RPS": "readiness",
    # 手册（趋势手册 / 套利手册）
    "手册": "manual",
}


@dataclass
class Intent:
    """parse_intent 的结构化产出（供 T5 组上下文、T6 组溯源）。"""

    symbols: list[dict] = field(default_factory=list)
    # 每项 {"bare": "600519", "ts_code": "600519.SH", "name": "贵州茅台"}
    topics: list[str] = field(default_factory=list)
    # 主题 key 去重列表，顺序稳定（= TOPIC_KEYWORDS 首次出现顺序）
    matched_keywords: dict[str, str] = field(default_factory=dict)
    # 命中的关键词 → 主题（多对一，如实保留每个命中词）
    is_general: bool = True
    # 既无股票也无主题 → 通用问答意图，供 T6 的 system prompt 兜底处理
    intraday: bool = False
    # 消息引用“今天/今日/盘中/现在”或显式日期=今天（如“14号”恰为当日）——
    # build_context 据此在交易时段注入【盘中/当日实时】块（2026-10-14：
    # 修“问14号无法回答”：此前只有收盘口径块，盘中问当日一律被 LLM 答没数据）


# 当日引用关键词（_norm 去空白归一后子串判断）。“实时/现在/盘中”等词本身
# 即含当日语义；周末/盘前是否真注入由 build_context 的 _intraday_now 时钟闸把关。
_TODAY_KEYWORDS = ("今天", "今日", "盘中", "现在", "此刻", "实时")


def _refers_today(msg: str) -> bool:
    """消息（去空白 NFKC）是否引用“今天”。

    显式日期只在**恰为今天**时命中（“14号”只有 14 号当天算；其他日子的历史
    问题走收盘口径块）。匹配顺序：完整年月日 → 年月日/月日斜杠 → X月X →
    X号/X日 → 4 位 MMDD。8 位日期串（20261013）前后都是数字，任何模式都匹配
    不上——沿用代码扫描同款的 (?<!\d)/(?!\d) 边界。
    """
    if any(k in msg for k in _TODAY_KEYWORDS):
        return True
    t = date.today()
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", msg)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))) == \
               (t.year, t.month, t.day)
    m = re.search(r"(\d{1,2})月(\d{1,2})", msg)
    if m and (int(m.group(1)), int(m.group(2))) == (t.month, t.day):
        return True
    m = re.search(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)", msg)
    if m and (int(m.group(1)), int(m.group(2))) == (t.month, t.day):
        return True
    m = re.search(r"(?<!\d)(\d{1,2})[号日]", msg)
    if m and int(m.group(1)) == t.day:
        return True
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", msg)
    return bool(m and m.group(1) == t.strftime("%m%d"))


def parse_intent(message: str, cfg: dict) -> Intent:
    """识别用户消息里的股票标的与主题意图。

    - 股票识别：6 位代码正则（symbols.normalize 归一，只留 is_a_share_stock 的）
      + hithink 全 A 名称词典子串扫描（归一名称 ≥2 字；按名称长度从长到短匹配，
      命中后等长挖除防短名吞子串；代码命中优先登记，与名称命中按 ts_code 去重）。
      词典每次调用现取（与 pool 一致，不自建缓存）。
    - 主题识别：TOPIC_KEYWORDS 子串匹配。
    - 两者皆无 → is_general=True（通用问答，交 T6 的 system prompt 处理）。
    """
    msg = _norm(message or "")  # 归一 + 去空白：关键词与名称扫描用
    msg_codes = unicodedata.normalize("NFKC", message or "")  # 只归一不去空白：代码扫描用

    matched: dict[str, str] = {}
    if msg:
        low = msg.lower()  # 中文不受影响；"rps" 小写也能命中 "RPS"
        for kw, topic in TOPIC_KEYWORDS.items():
            if kw.lower() in low:
                matched[kw] = topic
    topics = list(dict.fromkeys(matched.values()))

    symbols = _find_symbols(msg, msg_codes, cfg)
    return Intent(
        symbols=symbols,
        topics=topics,
        matched_keywords=matched,
        is_general=not symbols and not topics,
        intraday=_refers_today(msg),
    )


def _find_symbols(msg: str, msg_codes: str, cfg: dict) -> list[dict]:
    """代码正则 + 名称词典扫描，返回去重后的股票列表（代码命中顺序优先）。

    msg：去空白归一文本（名称扫描）；msg_codes：保留分隔符的 NFKC 文本（代码扫描）。
    """
    if not msg and not msg_codes:
        return []
    if not any(ch.isalnum() for ch in msg_codes):
        # 纯空白/纯标点：既无 6 位代码也无中文名可命中——在加载全量名称词典
        # （数千行）之前短路，空转消息不白付词典 IO
        return []

    names_df = md_symbol_names(cfg)  # 列：symbol(600519.SH) / name；每次现取
    name_of: dict[str, str] = {
        r["symbol"]: r["name"] for r in names_df.to_dicts()
        if r.get("symbol") and r.get("name")
    }

    found: dict[str, dict] = {}

    def note(ts_code: str, bare: str, name: str | None) -> None:
        if ts_code not in found:
            found[ts_code] = {"bare": bare, "ts_code": ts_code, "name": name}

    # 1) 6 位代码（显式提及，先于名称扫描登记；在保留分隔符的文本上扫）
    for bare in _CODE_RE.findall(msg_codes):
        try:
            info = normalize(bare)
        except ValueError:
            continue  # 不是股票代码（如 100000 判不出交易所）
        if not is_a_share_stock(info.ts_code):
            continue  # 只留 A 股，剔 B 股/基金/债券
        note(info.ts_code, info.bare, name_of.get(info.ts_code))

    # 2) 名称词典扫描：归一名称按长度降序（长名优先，防 "XX科技" 之类吞掉子串），
    #    命中即等长挖除（同一段文字被长名占走后，短名不再二次命中）
    name_row: dict[str, str] = {}
    for sym, n in name_of.items():
        nn = _norm(n)
        if len(nn) >= 2 and nn not in name_row:
            name_row[nn] = sym
    remaining = msg
    for nn in sorted(name_row, key=lambda s: (-len(s), s)):
        if nn not in remaining:
            continue
        remaining = remaining.replace(nn, _BLANK * len(nn))
        sym = name_row[nn]
        if not is_a_share_stock(sym):
            continue  # 词典含指数/基金等，非 A 股名称不进意图
        bare = sym.split(".")[0]
        note(sym, bare, name_of.get(sym))

    return list(found.values())


# ---------------------------------------------------------------------------
# T5：上下文组装 build_context
# ---------------------------------------------------------------------------

# text 预算（汉字量级）。超预算先整体去掉"序列/备注"行（extra），仍超再截断——
# 正文优先 模式/就绪度/主线（这些都在各块 core 里），序列类只贴最新几期。
# T15 耗时优化：4000 → 2800（简报批准区间 ~2500-3000）；正常数据 core+extra 在
# 2000 字上下，此值主要收紧行为上限，防止序列/长 playbook 把注入喂肥。
CONTEXT_CHAR_BUDGET = 2800

# topic → 数据块（顺序 = 各主题块序，并集后按 _BLOCK_ORDER 规范排序）。
# 相对任务简报映射的两处微调（已在 T5 报告记录）：
# 1) limit_up/ladder 增加"情绪五指标"——涨停计数/梯队档位/晋级率在
#    sentiment_metrics.latest 里，框架报告只有 counts 没有梯队明细；
# 2) 出现任意 intent.symbols 时额外注入 framework（模式闸门）+ manual
#    （个股问题几乎总要"就绪≠可买"的口径）+ quotes + smc。
_TOPIC_BLOCKS: dict[str, tuple[str, ...]] = {
    "mode": ("framework", "sentiment"),
    "quadrant": ("framework", "sentiment"),
    "sentiment": ("framework", "sentiment"),
    "limit_up": ("framework", "sentiment"),
    "ladder": ("framework", "sentiment"),
    "mainline": ("framework", "turnover"),
    "review": ("framework",),
    "readiness": ("framework", "manual"),
    "manual": ("framework", "manual"),
}

# 数据块规范注入顺序（与下方 builders 字典 / _TITLES 的键一致）
_BLOCK_ORDER = ("intraday", "framework", "sentiment", "turnover", "manual",
                "quotes", "smc", "general")

_TITLES = {
    "intraday": "盘中实时",
    "framework": "框架报告",
    "sentiment": "情绪五指标",
    "turnover": "成交结构主线",
    "manual": "手册就绪度",
    "quotes": "个股行情",
    "smc": "个股SMC结构",
    "general": "系统能力说明",
}


@dataclass
class ContextResult:
    """build_context 的产出（T6 直接透传 asof/used_blocks 并组 system prompt）。"""

    text: str  # 给 LLM 的上下文正文（markdown，每块带标题与基准日）
    used_blocks: list[str]  # 实际注入的数据块名（含基准日，溯源用）
    asof: str  # 各块基准日最大值；全缺为今天（缺失说明在 text 里）


@dataclass
class _BlockOut:
    """单个数据块的组装结果（内部结构，供 _render 排版）。"""

    title: str  # 标题行（已含基准日/口径标注）
    used_label: str | None  # used_blocks 条目；None = 缺失不计入
    asof: str | None  # 该块基准日（YYYY-MM-DD）；缺失 None
    core: list[str]  # 必保正文行
    extra: list[str] = field(default_factory=list)  # 预算富余才注入（序列/备注）
    missing: str | None = None  # 非 None = 该块缺失，text 写"该数据缺失"


def build_context(intent: Intent, cfg: dict) -> ContextResult:
    """按意图组装给 LLM 的上下文（确定性注入，计划第四节）。

    - text：markdown 正文。每个数据块带标题与基准日；块缺失时写明"该数据缺失"，
      不静默省略。
    - used_blocks：实际注入的数据块名（"框架报告 2026-10-10" 这种带基准日的形态）。
    - asof：各块基准日的最大值；全都缺则为今天（此时依赖 text 里的缺失说明）。

    每个数据块独立 try/except：缺文件/坏 JSON/远端异常只降级该块为缺失说明，
    build_context 本身不抛（T4 审查移交项）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    symbols = intent.symbols or []
    topics = set(intent.topics or [])

    want: set[str] = set()
    # 消息引用当日且时钟在交易日 9:30 后 → 注入盘中/当日实时块（14号问题修复）
    if intent.intraday and _intraday_now():
        want.add("intraday")
    if intent.is_general or (not topics and not symbols):
        want.add("general")
    else:
        for topic in topics:
            want.update(_TOPIC_BLOCKS.get(topic, ("framework",)))
        if symbols:
            want.update({"framework", "manual", "quotes", "smc"})
    keys = [k for k in _BLOCK_ORDER if k in want] or ["general"]

    builders = {
        "intraday": _block_intraday,
        "framework": _block_framework,
        "sentiment": _block_sentiment,
        "turnover": _block_turnover,
        "manual": _block_manual,
        "quotes": _block_quotes,
        "smc": _block_smc,
        "general": _block_general,
    }
    blocks: list[_BlockOut] = []
    for key in keys:
        try:
            out = builders[key](cfg, intent)
        except Exception as e:  # noqa: BLE001 — 单块失败降级为缺失说明，不许拖垮整体
            out = _missing_block(key, f"{type(e).__name__}: {e}")
        blocks.append(out)

    used = [b.used_label for b in blocks if b.used_label]
    dates = [b.asof for b in blocks if b.asof]
    return ContextResult(
        text=_render(blocks),
        used_blocks=used,
        asof=max(dates) if dates else today,
    )


def _data_root(cfg: dict) -> Path:
    """本地数据根：优先 cfg 的 hithink_data.store_dir（绝对路径，与 manuals/
    market 同口径、不受运行目录影响），缺省回退本仓库 data/（与 scripts/* 落档同处）。"""
    root = resolve_path((cfg.get("hithink_data") or {}).get("store_dir"))
    return root if root is not None else Path(__file__).resolve().parents[2] / "data"


def _read_json(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))  # FileNotFoundError 等上抛给块级兜底
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name} 顶层不是对象")
    return doc


def _missing_block(key: str, reason: str) -> _BlockOut:
    reason = " ".join(str(reason).split())
    if len(reason) > 120:
        reason = reason[:120] + "…"
    return _BlockOut(title=_TITLES[key], used_label=None, asof=None,
                     core=[], extra=[], missing=reason)


def _sym_label(s: dict, quote_name: str | None = None) -> str:
    """符号展示名：优先意图词典名 → 行情回填名 → 裸代码。"""
    name = s.get("name") or quote_name or ""
    bare = s.get("bare") or ""
    return f"{name}({bare})" if name else str(bare)


def _pct(v, digits: int = 1, scale: float = 100.0) -> str:
    """数值 → 百分比字符串（源数据里 break_rate 已是百分数、red_rate/晋级率是小数，
    各处按需传 scale；非数值给 —）。"""
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v * scale:.{digits}f}%"


# 趋势值 → 中文。SMC 结构块与手册块共用（两者趋势值同源——indicators.smc 的
# trend 字段），避免同一上下文里 "up" 与 "上行" 中英混杂。
_TREND_CN = {"up": "上行", "down": "下行", "range": "震荡", "None": "未定"}


def _cn_trend(v) -> str:
    return _TREND_CN.get(str(v), str(v))  # None → "未定"（尚无 BOS/CHOCH 事件）


def _intraday_now(now: datetime | None = None) -> bool:
    """工作日 9:30 起的“当日”窗口（含盘中与盘后当日段）——实时块注入门槛。

    9:30 前 kph 还是上一交易日值（注入会错标当日）、周末为上周五值 → 都不注。
    （节假日沿用工作日判断：kph 回吐上一交易日——与看板 kph 覆盖同款边界，接受。）
    """
    now = now or datetime.now()
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 30)


def _block_intraday(cfg: dict, intent: Intent) -> _BlockOut:
    """盘中/当日实时（开盘红情绪 + 腾讯指数快照）——“今天/X号”当日问题的口径。

    kph 失败 → 抛出走块级缺失降级；指数失败只丢指数行（情绪是主口径）。
    开盘红实时端点口径：盘中=当日实时值，15:00 后=当日全天值。
    """
    from ..adapters.tencent import TencentAdapter
    from ..kph_client import market_emotion

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H:%M")

    e = market_emotion()
    if e.get("up") is None and e.get("limit_up") is None:
        raise RuntimeError("开盘红市场情绪接口无数据")

    def _v(x):
        return x if x is not None else "—"

    core = [
        f"- 当日涨跌家数（开盘红实时）：上涨 {_v(e.get('up'))} / "
        f"平盘 {_v(e.get('flat'))} / 下跌 {_v(e.get('down'))}",
        f"- 涨停/跌停（开盘红实时）：实际非 ST {_v(e.get('limit_up'))}/{_v(e.get('limit_down'))}，"
        f"含 ST {_v(e.get('limit_up_incl_st'))}/{_v(e.get('limit_down_incl_st'))}",
        "- 口径：9:30-15:00 为盘中实时（数字随盘变动）；15:00 后为当日全天值。",
        "- 回答“今天/今日/当日/X号”问题时优先引用本块（注明取数时刻）；"
        "收盘口径块仅作历史对照。",
    ]
    try:
        dim = TencentAdapter(cfg.get("tencent") or {}).snapshot(
            ["000001.SH", "399001.SZ", "399006.SZ", "000680.SH"])
        data = dim.data if isinstance(getattr(dim, "data", None), dict) else {}
        parts = []
        for it in data.get("item") or []:
            if not isinstance(it, dict):
                continue
            p, c = it.get("price"), it.get("change_pct")
            if isinstance(p, (int, float)) and isinstance(c, (int, float)):
                parts.append(f"{it.get('name') or it.get('symbol')} {p:.2f}（{c:+.2f}%）")
        if parts:
            core.append("- 指数实时（腾讯）：" + "；".join(parts))
    except Exception:  # noqa: BLE001 — 指数是增强项，失败不拖垮情绪主块
        pass

    return _BlockOut(title=f"盘中/当日实时（{today} {stamp} 取数）",
                     used_label=f"盘中实时 {today}", asof=today,
                     core=core, extra=[], missing=None)


def _block_framework(cfg: dict, intent: Intent) -> _BlockOut:
    """框架报告摘要：模式判定、四象限、三信号、情绪快照、风险/操作提示（精简字段）。"""
    fw = _read_json(_data_root(cfg) / "framework_report_latest.json")
    asof = str(fw.get("asof") or "")
    if not asof:
        raise ValueError("framework_report_latest.json 缺 asof")

    core: list[str] = []
    core.append(f"- 模式判定：{fw.get('mode') or '缺'}")
    core.append(f"- 四象限：{fw.get('quadrant') or '缺'}")

    triad = fw.get("triad")
    if isinstance(triad, dict) and triad:
        parts = []
        for k, v in triad.items():
            if not isinstance(v, dict):
                continue
            seg = f"{k}={v.get('status') or '—'}"
            if v.get("note"):
                seg += f"（{v.get('note')}）"
            parts.append(seg)
        if parts:
            conf = fw.get("triad_confirmed")
            core.append(f"- 三信号（共振 {conf if conf is not None else '?'}/3）："
                        + "；".join(parts))

    l2 = fw.get("layer2_emotion")
    if isinstance(l2, dict) and l2:
        c = l2.get("counts") or {}
        line = (f"- 情绪：分 {l2.get('regime_score')}（{l2.get('state')}），"
                f"阶段 {l2.get('phase')}；涨停{c.get('zt')}/炸板{c.get('zb')}/"
                f"跌停{c.get('dt')}，最高{c.get('max_height')}板")
        p12 = ((l2.get("promo") or {}).get("1->2") or {}).get("rate")
        if isinstance(p12, (int, float)):
            line += f"，晋级1→2 {_pct(p12)}"
        core.append(line)

    flags = fw.get("flags")
    if isinstance(flags, list) and flags:
        core.append("- 风险提示：" + "；".join(str(x) for x in flags))
    playbook = fw.get("playbook")
    if isinstance(playbook, list) and playbook:
        core.append("- 操作提示（手册口径）：" + "；".join(str(x) for x in playbook))

    if not core:
        raise ValueError("framework_report_latest.json 无可摘字段")

    extra: list[str] = []
    l1 = fw.get("layer1_style")
    if isinstance(l1, dict) and l1:
        rel = l1.get("relative") or {}
        vol = l1.get("volatility") or {}
        seg = []
        if rel:
            seg.append(f"相对收益 {rel.get('pair')} {rel.get('direction')}"
                       f"（MA5 {rel.get('ma5')} vs MA20 {rel.get('ma20')}，"
                       f"5日差 {rel.get('ret5_gap')}pp）")
        if vol:
            seg.append(f"波动率 {vol.get('value')}%（{vol.get('percentile')}分位）")
        if seg:
            extra.append("- 第一层风格：" + "；".join(seg))

    return _BlockOut(title=f"框架报告（asof {asof}，收盘口径）",
                     used_label=f"框架报告 {asof}", asof=asof,
                     core=core, extra=extra)


def _block_sentiment(cfg: dict, intent: Intent) -> _BlockOut:
    """情绪五指标：贴 latest 全量一行 + 近 5 期涨停/炸板率序列（extra）。"""
    doc = _read_json(_data_root(cfg) / "sentiment_metrics.json")
    asof = str(doc.get("asof") or "")
    if not asof:
        raise ValueError("sentiment_metrics.json 缺 asof")
    latest = doc.get("latest")
    if not isinstance(latest, dict) or not latest:
        raise ValueError("sentiment_metrics.json 缺 latest")

    ladder = latest.get("ladder") or {}
    promo = latest.get("promo") or {}
    yzt = latest.get("yzt") or {}
    line = (f"- 最新（{latest.get('date') or asof}）：涨停{latest.get('zt')} "
            f"炸板{latest.get('zb')} 跌停{latest.get('dt')}，"
            f"炸板率{_pct(latest.get('break_rate'), scale=1)}，"
            f"最高{latest.get('max_height')}板；"
            f"梯队 1板{ladder.get('1', '—')}/2板{ladder.get('2', '—')}/"
            f"3板{ladder.get('3', '—')}")
    p12 = (promo.get("1->2") or {}).get("rate")
    p23 = (promo.get("2->3") or {}).get("rate")
    if isinstance(p12, (int, float)) or isinstance(p23, (int, float)):
        line += (f"；晋级 1→2 {_pct(p12)}、2→3 {_pct(p23)}")
    if isinstance(yzt.get("red_rate"), (int, float)):
        line += f"；涨停股红盘率 {_pct(yzt.get('red_rate'))}"
    core = [line]

    extra: list[str] = []
    series = [x for x in (doc.get("series") or []) if isinstance(x, dict)]
    if series:
        tail = series[-5:]
        seg = "、".join(
            f"{str(x.get('date') or '?')[5:]} 涨停{x.get('zt')} 炸板率{_pct(x.get('break_rate'), scale=1)}"
            for x in tail)
        extra.append(f"- 近{len(tail)}期：{seg}")

    return _BlockOut(title=f"情绪五指标（asof {asof}，收盘口径）",
                     used_label=f"情绪五指标 {asof}", asof=asof,
                     core=core, extra=extra)


def _block_turnover(cfg: dict, intent: Intent) -> _BlockOut:
    """成交结构主线：主线概念 TOP5 + 行业 TOP5 + 总额；第一主线序列做 extra。"""
    doc = _read_json(_data_root(cfg) / "turnover_structure.json")
    asof = str(doc.get("asof") or "")
    if not asof:
        raise ValueError("turnover_structure.json 缺 asof")

    concepts = [c for c in (doc.get("mainline_concepts") or []) if isinstance(c, dict)]
    if not concepts:
        raise ValueError("turnover_structure.json 缺主线概念")
    seg = []
    for c in concepts[:5]:
        s = f"{c.get('concept')} {_pct(c.get('share_pct'), scale=1)}"
        d = c.get("delta_pp")
        if isinstance(d, (int, float)):
            s += f"（{d:+.2f}pp）"
        seg.append(s)
    core = ["- 主线概念 TOP5（成交占比/环比）：" + "、".join(seg)]

    industries = [i for i in (doc.get("industry_top5") or []) if isinstance(i, dict)]
    if industries:
        core.append("- 行业成交 TOP5："
                    + "、".join(f"{i.get('industry')} {_pct(i.get('share_pct'), scale=1)}"
                               for i in industries[:5]))
    total = doc.get("total_amount_raw")
    if isinstance(total, (int, float)):
        core.append(f"- 两市成交额：{total / 1e12:.2f} 万亿")

    extra: list[str] = []
    series = [x for x in (doc.get("top1_concept_series") or []) if isinstance(x, dict)]
    if series:
        tail = series[-5:]
        extra.append("- 第一主线占比近"
                     f"{len(tail)}期：" + "、".join(
                         f"{str(x.get('date') or '?')[5:]} {_pct(x.get('share_pct'), scale=1)}"
                         for x in tail))

    return _BlockOut(title=f"成交结构主线（asof {asof}，收盘口径）",
                     used_label=f"成交结构主线 {asof}", asof=asof,
                     core=core, extra=extra)


def _block_manual(cfg: dict, intent: Intent) -> _BlockOut:
    """手册就绪度：只贴 intent.symbols 相关股 + 全市场就绪统计（不贴全表）。"""
    try:
        from ..manuals import get_flags
    except ImportError:  # 免费版：manuals 已剔除
        return _BlockOut(title="手册就绪度（免费版不含此功能）",
                         used_label="手册就绪度（免费版不含）", asof="",
                         core=["免费版不含手册就绪度功能"], extra=[])

    doc = get_flags(cfg)
    asof = doc.get("date")
    flags = doc.get("flags") or {}
    if not asof:
        raise ValueError("manual_flags 不可用（无缓存且计算失败）")

    core: list[str] = []
    for s in intent.symbols or []:
        f = flags.get(s.get("ts_code") or "")
        if not f:
            core.append(f"- {_sym_label(s)}：无就绪度记录（未入统计样本）")
            continue
        d_trend = _cn_trend(f.get("d_trend")) if f.get("d_trend") else "—"
        w_trend = _cn_trend(f.get("w_trend")) if f.get("w_trend") else "—"
        core.append(
            f"- {_sym_label(s)}：RPS {f.get('rps')}，日线{d_trend}"
            f"/周线{w_trend}，连板{f.get('limit_days') or 0} → "
            f"趋势{'就绪' if f.get('trend_ready') else '未就绪'}、"
            f"套利{'就绪' if f.get('arb_ready') else '未就绪'}")

    stocks = doc.get("stocks") or len(flags)
    trend_n = sum(1 for v in flags.values() if isinstance(v, dict) and v.get("trend_ready"))
    arb_n = sum(1 for v in flags.values() if isinstance(v, dict) and v.get("arb_ready"))
    core.append(f"- 全市场（{stocks} 只）：趋势就绪 {trend_n} 只、套利就绪 {arb_n} 只；"
                "就绪≠可买，出手与否看模式闸门")

    return _BlockOut(title=f"手册就绪度（asof {asof}，收盘口径）",
                     used_label=f"手册就绪度 {asof}", asof=str(asof),
                     core=core, extra=[])


def _block_quotes(cfg: dict, intent: Intent) -> _BlockOut:
    """个股实时行情（腾讯快照，底层直调不过 HTTP 自己）；盘中必标"盘中"。"""
    from ..adapters.astock_data import AStockDataAdapter

    symbols = intent.symbols or []
    if not symbols:
        raise ValueError("无意图个股")

    dim = AStockDataAdapter(cfg.get("a_stock_data") or {}).tencent_quote(
        [str(s.get("bare")) for s in symbols])
    data = dim.data if isinstance(dim.data, dict) else {}
    items = data.get("item") or []
    if not items:
        err = (dim.error or {}).get("message") if isinstance(dim.error, dict) else None
        raise RuntimeError(err or "腾讯行情返回空")
    by_code = {str(it.get("symbol")): it for it in items if isinstance(it, dict)}

    now = datetime.now()
    # 盘中判定：工作日 9:30-15:00 且行情日期=今天（节假日腾讯回吐昨日日期 → 自动落收盘）
    intraday = (now.weekday() < 5 and (now.hour, now.minute) >= (9, 30)
                and (now.hour, now.minute) < (15, 0))
    today = now.strftime("%Y-%m-%d")

    core: list[str] = []
    dates: list[str] = []
    for s in symbols:
        it = by_code.get(str(s.get("bare")))
        if not it:
            core.append(f"- {_sym_label(s)}：实时行情未返回")
            continue
        digits = re.sub(r"\D", "", str(it.get("time") or ""))
        if len(digits) >= 8:
            d = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            dates.append(d)
            if d == today and intraday:
                stamp = (f"盘中 {digits[8:10]}:{digits[10:12]}"
                         if len(digits) >= 12 else "盘中")
            else:
                stamp = f"收盘快照 {d}"
        else:
            stamp = "时间未知"
        price = (f"{it['price']:.2f}"
                 if isinstance(it.get("price"), (int, float)) else "—")
        chg = (f"{it['change_pct']:+.2f}%"
               if isinstance(it.get("change_pct"), (int, float)) else "—")
        seg = f"- {_sym_label(s, it.get('name'))}：{price} 元（{chg}"
        if isinstance(it.get("turnover_ratio"), (int, float)):
            seg += f"，换手 {it['turnover_ratio']:.2f}%"
        seg += f"）（{stamp}）"
        core.append(seg)

    if not dates:
        raise ValueError("行情缺时间戳，无法标注基准日")
    asof = max(dates)
    period = "盘中" if intraday and asof == now.strftime("%Y-%m-%d") else "收盘"
    return _BlockOut(
        title=f"个股行情（{period}，asof {asof}）",
        used_label=f"个股行情 {asof}（{period}）", asof=asof,
        core=core, extra=[])


def _block_smc(cfg: dict, intent: Intent) -> _BlockOut:
    """个股 SMC 结构：日线/周线趋势结论（core.kline 取K线 → indicators.smc 判定）。"""
    from ..core.kline import local_kline_dim
    from ..indicators import smc
    MIN_BARS, MIN_WEEK_BARS = 80, 12  # 免费版：manuals 已剔除，常量内联

    symbols = intent.symbols or []
    if not symbols:
        raise ValueError("无意图个股")

    core: list[str] = []
    dates: list[str] = []
    source: str | None = None
    for s in symbols:
        try:
            dim = local_kline_dim(cfg, normalize(str(s.get("bare"))), days=160)
            rows = [r for r in ((dim.data or {}).get("rows") or [])
                    if isinstance(r, dict) and r.get("date")
                    and r.get("open") is not None and r.get("close") is not None
                    and r.get("high") is not None and r.get("low") is not None]
            if source is None:
                source = (dim.data or {}).get("source")
            if len(rows) < MIN_BARS:
                core.append(f"- {_sym_label(s)}：K线仅 {len(rows)} 根"
                            f"（<{MIN_BARS}），结构判定跳过")
                continue
            ds = [str(r["date"]) for r in rows]
            o = [float(r["open"]) for r in rows]
            h = [float(r["high"]) for r in rows]
            lo = [float(r["low"]) for r in rows]
            c = [float(r["close"]) for r in rows]
            d_rep = smc.smc_report_frame(ds, o, h, lo, c, k=5)
            dates.append(str(d_rep.get("asof") or ds[-1]))
            d_trend = _cn_trend(d_rep.get("trend"))
            wd, wo, wh, wl, wc = smc.resample_weekly(ds, o, h, lo, c)
            if len(wc) >= MIN_WEEK_BARS:
                w_rep = smc.smc_report_frame(wd, wo, wh, wl, wc, k=3)
                w_trend = _cn_trend(w_rep.get("trend"))
            else:
                w_trend = f"样本不足（{len(wc)}周）"
            core.append(f"- {_sym_label(s)}：日线结构 {d_trend}"
                        f"（{d_rep.get('asof')}）；周线结构 {w_trend}")
        except Exception as e:  # noqa: BLE001 — 单票失败不拖垮其余个股
            core.append(f"- {_sym_label(s)}：结构计算失败"
                        f"（{type(e).__name__}: {str(e)[:60]}）")

    ok_lines = [ln for ln in core if "结构计算失败" not in ln and "结构判定跳过" not in ln]
    if not ok_lines:
        raise RuntimeError("全部个股 SMC 结构计算失败")
    if source:
        core.append(f"- K线源：{source}")
    asof = max(dates) if dates else None
    if not asof:
        raise RuntimeError("SMC 结构无基准日")
    return _BlockOut(title=f"个股SMC结构（asof {asof}，前复权收盘口径）",
                     used_label=f"个股SMC结构 {asof}", asof=asof,
                     core=core, extra=[])


def _block_general(cfg: dict, intent: Intent) -> _BlockOut:
    """通用意图：注入"系统能回答什么"，避免 LLM 裸答。"""
    return _BlockOut(
        title="系统能力说明",
        used_label="能力说明", asof=None,
        core=[
            "- 本助手只基于看板本地数据回答三层择时问题，可回答主题："
            "模式判定/四象限/三信号、情绪与赚钱效应、主线与板块轮动、"
            "涨停与连板梯队、大盘复盘、手册就绪度与 RPS、"
            "个股（实时行情、SMC 结构、趋势/套利手册就绪）。",
            "- 提问示例：“今天模式怎么样”“主线是谁”“涨停梯队到几板了”"
            "“600519 符合趋势手册吗”“帮我复盘今天的盘面”。",
            "- 每类数据都带基准日与口径标注（收盘口径/盘中）；数据没有的如实说没有，不编造。",
        ],
        extra=[])


def _render(blocks: list[_BlockOut]) -> str:
    """排版：缺失块写"该数据缺失"；预算超限先去掉 extra（序列/备注），再硬截断。"""

    def section(b: _BlockOut, with_extra: bool) -> str:
        if b.missing:
            return f"## {b.title}\n（该数据缺失：{b.missing}）"
        lines = list(b.core) + (list(b.extra) if with_extra else [])
        return f"## {b.title}\n" + "\n".join(lines)

    full = "\n\n".join(section(b, True) for b in blocks)
    if len(full) <= CONTEXT_CHAR_BUDGET:
        return full
    core_only = "\n\n".join(section(b, False) for b in blocks)
    if len(core_only) <= CONTEXT_CHAR_BUDGET:
        return core_only
    return (core_only[:CONTEXT_CHAR_BUDGET]
            + "\n\n…（上下文超预算已截断；优先保留模式/就绪度/主线字段）")
