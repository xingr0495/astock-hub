"""收盘核对（reconcile）v3：当日全市场数据入库后、下游看板更新前的核对 gate。

本地（A 股**非 ST**）vs 独立来源**开盘红市场情绪**（2026-10-12 起以其为准）。

v3 判定语义（2026-10-29 用户拍板"按你的意思做"）——从"两边比家数"改为
"**本地逐只验真 + 参考量级对照**"：

- 涨停/跌停（硬校验）：
  1. **逐只验真**：本地每个涨跌停信号用腾讯昨收（交易所除权参考价）+
     涨跌幅档 HALF_UP 复算理论价验证，现价与理论价一致（±1 分）才算真板；
     验证不过的条目剔除，> 5 只判本地数据有假信号 → 硬失败。
  2. **量级对照**：验真后的家数与参考源比，仅当本地**少于**参考超过
     max(10, 20%) 才硬失败（那才是真缺数据/错位）；本地多于参考永不
     因此失败——参考池有少报盲区（10-29 实测东财/开盘红双源同报 41、
     真实 50，含东山精密等 9 只大票漏报）。
- 上涨/下跌家数：advisory 记录不判定（2026-10-19 拍板，平盘边界口径漂移
  重灾区）；成交额口径不一致不参与对账。
- 验证源取数失败 → 保守硬失败（宁拦不放）；ST 名单不可用时如实标注
  advisory（此时口径可能含 ST，验真无法区分——名单来自 hithink REST，
  失败罕见且有日缓存）。

历史沿革：v1（±5 绝对差硬校验，10-12 抓出 ST 口径）→ v2（家数降 advisory
+10-29 本地多于参考时临时验真豁免）→ v3（验真成为判定主体）。
参考源不可用（开盘红 L2 失败）时降级跳过并告警，不拦停数据链。
"""

from __future__ import annotations

import polars as pl
import requests as _requests

from ..indicators.pipeline import compute_limit_signals
from ..indicators.price_limits import polars_price_limit_pct
from ..kph_client import market_emotion
from ..store import market_data as md
from ..symbols import polars_is_a_share_stock

# 容差
_UPDOWN_RATIO = 0.025   # 涨跌家数（advisory）：差异 ≤ 2.5%——开盘红按四舍五入
                        # 涨跌幅计数，平盘边界与本地原始价差 ~1-2%
_LIMIT_MISSING_ABS = 10  # 涨跌停量级对照：本地验真后少于参考的绝对豁免
_LIMIT_MISSING_RATIO = 0.20  # 与 20% 相对豁免取大者（小计数日 ±几只是天然口径差）
_UNVERIFIED_FAIL = 5     # 逐只验真不过的条目 > N 判本地假信号（硬失败）


def _local_close(cfg: dict, as_of: str) -> dict:
    """本地当日 **A 股非 ST**（含 BJ）涨跌家数、涨跌停、成交额（记录用）。"""
    cal = md.calendar_days(cfg)
    before = [d for d in cal if d < as_of]
    start = before[-1] if before else as_of

    frame = md.market_frame(cfg, start, as_of, with_raw=True)
    # 导出帧不保证按 symbol/date 排序；shift().over() 与涨跌停都依赖组内顺序，必须先排序
    frame = frame.sort(["symbol", "date"])
    # A 股口径：全品种帧含转债/ETF/基金/指数，与开盘红（沪深京 A 股）对不齐
    frame = frame.filter(polars_is_a_share_stock(pl.col("symbol")))
    # 对齐参考源的非 ST 口径（st_symbols 名称含 ST、大写带交易所后缀，日缓存）；
    # 名单获取失败时返回空表 = 不剔除，对账退回 10-12 前的含 ST 行为
    try:
        st = [s.upper() for s in md.st_symbols(cfg)]
    except Exception:  # noqa: BLE001 — 参考口径拿不到不阻塞对账
        st = []
    st_list_empty = not st  # 名单为空 = 可能取数失败，口径可能含 ST（如实标注）
    if st:
        frame = frame.filter(
            ~pl.col("symbol").str.to_uppercase().is_in(st))
    frame = frame.with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1)
          .alias("change_pct"))
    frame = compute_limit_signals(frame)
    day = frame.filter(
        pl.col("date") == pl.lit(as_of).str.to_date("%Y-%m-%d"))

    cp = day["change_pct"]
    # 涨跌停明细（代码+涨跌幅档）：供 reconcile_close 在参考源少报时逐只
    # 独立验证用。pct 与信号同参重算（ST 已剔除 → is_risk_warning=False）。
    day = day.with_columns(
        polars_price_limit_pct(pl.col("symbol"), pl.col("date"), pl.lit(False))
          .alias("_pct"))
    _lu = day.filter(pl.col("signal_limit_up").fill_null(False))
    _ld = day.filter(pl.col("signal_limit_down").fill_null(False))
    return {
        "total": day.height,
        "up": int((cp > 0).sum()),
        "down": int((cp < 0).sum()),
        "limit_up": int(day["signal_limit_up"].fill_null(False).sum()),
        "limit_down": int(day["signal_limit_down"].fill_null(False).sum()),
        "amount": float(day["amount"].sum()),
        "st_list_empty": st_list_empty,
        "limit_up_symbols": [
            {"code": s.split(".")[0], "pct": p}
            for s, p in zip(_lu["symbol"].to_list(), _lu["_pct"].to_list())],
        "limit_down_symbols": [
            {"code": s.split(".")[0], "pct": p}
            for s, p in zip(_ld["symbol"].to_list(), _ld["_pct"].to_list())],
    }


def _kph_close() -> dict:
    """开盘红市场情绪（KPH_REALTIME 盘后=全天值）。"""
    d = market_emotion()
    if d.get("up") is None and d.get("limit_up") is None:
        raise RuntimeError("开盘红市场情绪无数据（接口空返回）")
    return {
        "total": None,
        "up": d.get("up"), "down": d.get("down"),
        "limit_up": d.get("limit_up"), "limit_down": d.get("limit_down"),
        "amount": None,   # 单位/范围口径不一致，不参与对账
    }


def _check(label: str, local, ref, *, kind: str) -> dict:
    diff = None
    ok = False
    if local is not None and ref is not None:
        diff = local - ref
        if kind == "ratio":       # 按参考量的相对差（涨跌家数，advisory）
            denom = max(abs(ref), 1)
            ok = abs(diff) / denom <= _UPDOWN_RATIO
    return {"local": local, "ref": ref, "diff": diff, "ok": ok}


def _limit_check(symbols: list[dict], unverified: list[str], ref) -> dict:
    """涨跌停 v3 判定：逐只验真 + 参考量级对照。

    - 验真不过的条目（unverified）> _UNVERIFIED_FAIL → 本地假信号，硬失败；
    - 验真后家数 vs 参考：本地少于参考超过 max(10, 20%) → 缺数据，硬失败；
      本地多于参考永不因此失败（参考池少报盲区，10-29 实测）。
    """
    verified = len(symbols) - len(unverified)
    diff = verified - ref if ref is not None else None
    if len(unverified) > _UNVERIFIED_FAIL:
        ok = False
        reason = f"验真不过 {len(unverified)} 只 > {_UNVERIFIED_FAIL}（本地假信号）"
    elif ref is None:
        ok = False
        reason = "参考缺失"
    else:
        miss_tol = max(_LIMIT_MISSING_ABS, int(ref * _LIMIT_MISSING_RATIO))
        ok = verified >= ref - miss_tol
        reason = "" if ok else (
            f"验真后 {verified} < 参考 {ref} − {miss_tol}（本地缺数据）")
    return {"local": len(symbols), "verified": verified,
            "unverified_n": len(unverified), "unverified": unverified[:20],
            "ref": ref, "diff": diff, "ok": ok, "reason": reason}


def _decide_pass(checks: dict) -> tuple[bool, list[str]]:
    """passed 判定。

    硬校验 = 涨跌停两项（v3：逐只验真 + 量级对照，判定逻辑在 _limit_check）；
    涨跌家数降为 advisory 记录不判定——平盘边界口径漂移重灾区（盘整/反弹日
    ±100+ 家漂移实测，10-19 用户拍板以开盘啦家数为标准，本地家数不再拦链）。

    返回 (passed, advisory_keys)；涨跌停项缺失按不过处理（宁拦不放坏数据）。
    """
    advisory = [k for k in ("up", "down")
                if not (checks.get(k) or {}).get("ok")]
    hard_ok = all((checks.get(k) or {}).get("ok")
                  for k in ("limit_up", "limit_down"))
    return hard_ok, advisory


def _verify_limit_extras(symbols: list[dict], direction: str) -> dict:
    """用腾讯行情的昨收（交易所除权参考价）逐只独立验证本地涨跌停名单。

    背景（2026-10-29 实测）：东财跌停池与开盘红 SJDT 双源同报 41 只，而本地
    50 只——多出的 9 只（含东山精密/中金公司等主板大票）收盘=理论跌停价、
    密封，是真实跌停，参考源池子有少报盲区。参考源只有家数没有名单，故用
    腾讯 pre_close（交易所口径昨收，已含除权）+ 涨跌幅档独立复算理论价：
    现价与理论价一致（容差 1 分，吸收四舍五入边界）= 真实涨跌停。

    返回 {"checked": n, "unverified": [codes]}。验证源取数失败抛异常——
    调用方保守维持硬失败（宁拦不放坏数据）。
    """
    if not symbols:
        return {"checked": 0, "unverified": []}
    from decimal import Decimal, ROUND_HALF_UP

    def _qcode(code: str) -> str:
        if code.startswith(("4", "8")) or code.startswith("92"):
            return "bj" + code
        return ("sh" if code.startswith(("6", "9")) else "sz") + code

    url = "http://qt.gtimg.cn/q=" + ",".join(_qcode(s["code"]) for s in symbols)
    r = _requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    r.encoding = "gbk"
    quotes: dict[str, tuple[float, float]] = {}
    for line in r.text.strip().rstrip(";").split(";"):
        p = line.split("~")
        if len(p) > 5 and p[2].isdigit():
            try:
                quotes[p[2]] = (float(p[3]), float(p[4]))  # 现价, 昨收(除权参考)
            except ValueError:
                continue
    unverified: list[str] = []
    for s in symbols:
        q = quotes.get(s["code"])
        pct = s.get("pct")
        if not q or not pct or pct <= 0:
            unverified.append(s["code"])
            continue
        price, pre = q
        if pre <= 0:
            unverified.append(s["code"])
            continue
        factor = (Decimal("1") - Decimal(str(pct))) if direction == "down" else (
            Decimal("1") + Decimal(str(pct)))
        theory = (Decimal(str(pre)) * factor).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        # 真实封板：现价=理论价（±1 分吸收四舍五入/行情最小变动边界）；
        # 偏差更大 = 本地用错误参考价造出的假信号，判未验证
        if abs(price - float(theory)) > 0.011:
            unverified.append(s["code"])
    return {"checked": len(symbols), "unverified": unverified}


def reconcile_close(cfg: dict, as_of: str) -> dict:
    """核对当日；返回 {as_of, passed, checks, local_amount}（v3 语义见模块头）。

    参考源（开盘红）不可用（L2 网络失败/接口变更）时返回 ref_unavailable=True、
    passed=None——**参考源没数据 ≠ 本地数据有错**，调用方应降级跳过并告警，
    而不是把全部检查判 MISMATCH 拦停数据链（2026-10-07/08 iFinD 额度耗尽的教训）。
    """
    local = _local_close(cfg, as_of)
    try:
        ref = _kph_close()
    except Exception as e:  # noqa: BLE001 — 参考源不可用
        checks = {
            "up": _check("上涨", local["up"], None, kind="ratio"),
            "down": _check("下跌", local["down"], None, kind="ratio"),
            "limit_up": _limit_check(local.get("limit_up_symbols") or [],
                                     [], None),
            "limit_down": _limit_check(local.get("limit_down_symbols") or [],
                                       [], None),
        }
        return {"as_of": as_of, "passed": None, "ref_unavailable": True,
                "ref_error": str(e)[:200], "local_total": local["total"],
                "local_amount": local["amount"], "checks": checks}

    # 涨跌停 v3：逐只验真（每方向一次腾讯批量查询，秒级）→ 量级对照
    checks = {
        "up": _check("上涨", local["up"], ref["up"], kind="ratio"),
        "down": _check("下跌", local["down"], ref["down"], kind="ratio"),
    }
    for key, direction in (("limit_up", "up"), ("limit_down", "down")):
        syms = local.get(f"limit_{direction}_symbols") or []
        try:
            v = _verify_limit_extras(syms, direction)
            checks[key] = _limit_check(syms, v["unverified"], ref[key])
        except Exception as e:  # noqa: BLE001 — 验证源失败保守硬失败
            checks[key] = _limit_check(syms, [s["code"] for s in syms],
                                       ref[key])
            checks[key]["ok"] = False
            checks[key]["reason"] = "验证源取数失败"
            checks[key]["verify_error"] = str(e)[:120]

    passed, advisory = _decide_pass(checks)
    # 有验真被剔除条目时无论结果都记 advisory（透明记录本地信号毛刺）
    for key in ("limit_up", "limit_down"):
        if checks[key]["unverified_n"] and key not in advisory:
            advisory.append(key)
    if local.get("st_list_empty"):
        advisory.append("st_list_empty")  # ST 名单不可用，口径可能含 ST

    return {
        "as_of": as_of, "passed": passed,
        "advisory": advisory,
        "note": ("对账 v3（2026-10-29）：涨跌停=本地逐只验真（腾讯昨收理论价）"
                 "+参考量级对照（仅本地少于参考超 max(10,20%) 硬失败，本地多于"
                 "参考永不因此失败——参考池有少报盲区）；涨跌家数 advisory；"
                 "成交额不参与"),
        "local_total": local["total"], "local_amount": local["amount"],
        "ref_source": "kaipanhong-market-emotion", "checks": checks,
    }
