# -*- coding: utf-8 -*-
"""定时链心跳与失败告警。

设计：docs/superpowers/specs/2026-10-23-chain-failure-alert-design.md

告警策略（2026-10-31 用户拍板"飞书除了不能正常运行的问题，其他不需要推送警告"）：
- **唯一告警出口 = 18:00 守夜人**，链内 finalize 不再即时告警（即时告警是
  历史噪音主源：重试步/软失败/日志锁/测试假链名全走它）。
- 数据链的"正常运行"判据 = market-1600/1745 **任一条把 framework 步跑成功**
  （gate 过、下游重建完成，链的最终目的达成）→ 其余一切静默：partial_failed、
  单链缺席、"已恢复"知会（info 级别整体取消）一律不发。
- 两条链都无 framework ok（都缺席/被杀/中止在框架前）→ 严重（真·不能正常运行）。
- 复盘推送未完成且无成功推送落档 → 严重（推送不能正常运行）。
- **链名白名单**：只有 CHAINS 三条链和 "test"（手动测试按钮）能发告警，
  c1/y 等测试假名永远发不出去（2026-10-24/29 实测泄漏教训）。

子命令（供 PowerShell 埋点与计划任务调用）：
  python -m astock_hub.chain_guard beat --chain C --step S [--fail] [--tail T]
  python -m astock_hub.chain_guard finalize --chain C
  python -m astock_hub.chain_guard watchdog
  python -m astock_hub.chain_guard test

铁律：本模块任何自身异常都不得影响数据链——cmd_* 层兜底吞掉、退出码恒为 0。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import tempfile
from datetime import datetime

from .config import PROJECT_ROOT, load_config

STATUS_DIR = PROJECT_ROOT / "data" / "chain_status"
CHAINS = ("market-1600", "market-1745", "review-push-1700")
CHAIN_TITLES = {"market-1600": "16:00 收盘数据链",
                "market-1745": "17:45 兜底链",
                "review-push-1700": "17:00 复盘推送"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _path(day: str):
    return STATUS_DIR / f"{day}.json"


def load_status(day: str) -> dict:
    p = _path(day)
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc.setdefault("chains", {})
            doc.setdefault("alerts_sent", [])
            doc["date"] = day
            return doc
        except (ValueError, OSError):
            pass  # 损坏档按空骨架重建（当日链重新积累心跳）
    return {"date": day, "host": socket.gethostname(),
            "chains": {}, "alerts_sent": []}


def _write_status(day: str, doc: dict) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATUS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(day))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record_beat(day: str, chain: str, step: str, ok: bool, tail: str,
                skipped: bool = False) -> None:
    """记录一步心跳。skipped=True=有意跳过/未就绪（非故障，如周末陈旧守卫
    跳过推送、自愈 exit 2）：不计入失败，outcome 不会因此变 partial_failed。"""
    doc = load_status(day)
    ch = doc["chains"].setdefault(chain, {
        "started_at": _now(), "ended_at": None,
        "pid": os.getpid(), "host": socket.gethostname(), "steps": []})
    step_ok = bool(ok or skipped)
    for s in ch["steps"]:
        if s["name"] == step:
            # 同一步多次 beat（如 tdx 6 轮重试每轮都 beat）：以最后一次为准，
            # attempts 自然累计——成功轮覆盖失败轮，但 attempts 保留重试痕迹
            s.update(ok=step_ok, skipped=bool(skipped), tail=(tail or "")[:300],
                     ended_at=_now(), attempts=s.get("attempts", 1) + 1)
            break
    else:
        ch["steps"].append({"name": step, "ok": step_ok, "skipped": bool(skipped),
                            "tail": (tail or "")[:300],
                            "started_at": _now(), "ended_at": _now(),
                            "attempts": 1})
    _write_status(day, doc)


def chain_outcome(doc: dict, chain: str) -> str:
    """none=无记录；running=有心跳未收尾（链被杀的特征）；ok=全步成功或有意跳过；
    partial_failed=收尾但有真失败步（skipped 不算失败）。"""
    ch = (doc.get("chains") or {}).get(chain)
    if not ch:
        return "none"
    if not ch.get("ended_at"):
        return "running"
    return "partial_failed" if any(
        not s.get("ok") and not s.get("skipped")
        for s in ch.get("steps", [])) else "ok"


def _is_trading_day(cfg: dict | None = None, day: str | None = None) -> bool:
    """交易日判定（告警专用，宁误报不漏报）。

    包一层新鲜度检查：本地日历陈旧、查询日超出覆盖范围时，公共函数会把
    未知日一律判 False（实测日历滞后一周时周五也被判非交易日→全周告警
    静默），因此 day > 日历最大日 时按"无法判定"处理（返回 True=按交易日，
    绝不用陈旧日历压制告警）；日历明确覆盖到的周末/节假日才返回 False。
    日历本身不可用（None/异常）同样按交易日处理。
    """
    from datetime import date
    from .market.board_flow import _is_trading_day as _board_is_trading
    day = day or date.today().isoformat()
    c = cfg or load_config()
    result = _board_is_trading(c, day)
    if result is None:
        return True  # 日历不可用 → 按交易日处理（宁误报不漏报）
    if result is True:
        return True
    # result=False：区分"日历明确覆盖的非交易日"与"日历陈旧未覆盖"
    try:
        from .store import market_data as md
        days = md.calendar_days(c)
        max_day = str(max(days)) if days else ""
        if day > max_day:
            return True  # 日历没覆盖到 day：陈旧，不能据此压制告警
    except Exception:  # noqa: BLE001
        return True
    return False


def finalize(day: str, chain: str, *, alert: bool = True,
             trading_day: bool | None = None) -> None:
    """收尾。**不再即时告警**（2026-10-31 用户拍板）。

    告警判定集中到 18:00 守夜人——它能综合 17:45 兜底结果，已恢复/噪音步/
    测试假链名一律静默，只有"当日重建真的没跑成"才推。alert/trading_day
    参数保留仅为调用方兼容（均不再生效）。心跳照写（审计需要）。
    """
    doc = load_status(day)
    ch = doc["chains"].setdefault(chain, {
        "started_at": _now(), "pid": os.getpid(),
        "host": socket.gethostname(), "steps": []})
    ch["ended_at"] = _now()
    _write_status(day, doc)


# ---------------- 告警构建/发送/防重 ----------------

def _alert_cfg() -> dict:
    from .notify.config import load_config
    return load_config()


def build_failure_message(day: str, chain: str, doc: dict, *, severe: bool) -> str:
    """链失败消息（markdown）。链无节点（缺席/被杀）时给简化文案。"""
    ch = (doc.get("chains") or {}).get(chain)
    title = CHAIN_TITLES.get(chain, chain)
    lvl = "严重" if severe else "异常"
    head = (f"**级别**：{lvl}\n**日期**：{day}\n"
            f"**主机**：{(ch or {}).get('host', socket.gethostname())}\n"
            f"**任务**：{title}\n")
    if not ch:
        return head + "**状态**：链无任何心跳记录（未运行/被终止）\n" \
               f"**排查入口**：data/（对应链日志）"
    bad = [s for s in ch.get("steps", []) if not s.get("ok")]
    names = " / ".join(s["name"] for s in bad) or "链未完成"
    tail = "\n".join(f"> {s['tail']}" for s in bad if s.get("tail"))[:400]
    return (head
            + f"**失败步骤**：{names}\n"
            + (f"**日志尾部**：\n{tail}\n" if tail else "")
            + "**排查入口**：data/market-1600.log（或对应链日志）")


def already_alerted(doc: dict, chain: str, kind: str = "fail") -> bool:
    """防重键：失败 <chain>@；恢复 <chain>:recovered@。"""
    pref = f"{chain}:recovered@" if kind == "recovered" else f"{chain}@"
    return any(x.startswith(pref) for x in doc.get("alerts_sent", []))


def _gate_reason(n: dict) -> str | None:
    """闸门检查：通过返回 None，否则返回具体原因（给人看）。

    只认开关与地址两道闸。~~content_filters.alert~~ 2026-10-24 拆除：该键是
    旧「价格告警」内容勾选，与链失败告警无关，复用它会把配好的地址挡在门外
    （用户实测踩中）。
    """
    if not n.get("alert_enabled"):
        return "告警开关未打开（推送设置页勾选「启用定时链失败告警」）"
    if not n.get("alert_webhook_url"):
        return "告警地址为空（填入告警专用机器人 Webhook URL）"
    return None


def send_alert_detailed(day: str, chain: str, content: str, *, kind: str = "fail",
                        dedup: bool = True,
                        cfg: dict | None = None) -> tuple[bool, str | None]:
    """同 send_alert 但返回 (ok, 具体原因)。cfg 显式传入时覆盖已保存配置
    （测试按钮用表单当前值，免去"先保存才能测"的绕路）。

    链名白名单（2026-10-31；2026-11-07 增 data-src；2026-11-11 增
    paper-engine 引擎心跳守望；2026-11-12 增 minute-store 分钟抓取守望）：
    只放行三条真实链、"test"（手动测试按钮）、"data-src"（数据源风控告警，
    11-07 拍板）、"paper-engine"（模拟盘引擎心跳，11-11 拍板）与
    "minute-store"（分钟数据抓取守望，11-12 规格）——c1/y 等测试假名在
    真实状态目录上触发心跳也永远发不出去。
    """
    if chain not in CHAINS and chain not in ("test", "data-src",
                                             "paper-engine", "minute-store"):
        return False, f"未知链名 {chain}（告警只认 {', '.join(CHAINS)}），不发送"
    n = cfg if cfg is not None else _alert_cfg()
    reason = _gate_reason(n)
    if reason:
        return False, reason
    doc = load_status(day)
    tag = ":recovered@" if kind == "recovered" else "@"
    if dedup and already_alerted(doc, chain, kind):
        return False, "本日已发过同类告警（防重）"
    key = f"{chain}{tag}{datetime.now().isoformat(timespec='seconds')}"
    try:
        from .notify.sender import create_sender
        r = create_sender(n["alert_webhook_url"],
                          n.get("alert_channel")).send(content, "astock-hub 定时链告警")
        if not r.get("ok"):
            return False, f"发送失败（webhook 返回 {r.get('detail')}）"
    except Exception as e:  # noqa: BLE001
        print(f"chain_guard 告警发送失败（已忽略）：{e}")
        return False, f"发送失败：{str(e)[:120]}"
    if not dedup:
        return True, None  # 测试消息：发成功即可，不占防重键
    doc = load_status(day)  # 发送耗时期间可能有新心跳，重读合并防重键
    doc.setdefault("alerts_sent", []).append(key)
    _write_status(day, doc)
    return True, None


def send_alert(day: str, chain: str, content: str, *, kind: str = "fail",
               dedup: bool = True) -> bool:
    """三闸门（开关∧地址∧内容闸门）+ 防重 + 发送；发送异常吞掉记日志。

    dedup=False 用于测试按钮（同日重复点击照发，不占用防重键）。
    铁律：告警自身失败绝不影响数据链。
    """
    return send_alert_detailed(day, chain, content, kind=kind, dedup=dedup)[0]


# ---------------- 守夜人 ----------------

def _review_pushed_today(day: str) -> bool:
    p = PROJECT_ROOT / "data" / "review_pushed.json"
    try:
        return day in (json.loads(p.read_text(encoding="utf-8")).get("dates") or [])
    except Exception:  # noqa: BLE001
        return False


def _rebuilt_ok(doc: dict, chain: str) -> bool:
    """该链是否把 framework 步跑成功过（= 当日数据重建完成的代理判据）。

    framework 是下游重建链（gate→membership→regime→framework）的终点站：
    它 ok ⇒ 对账过、成分/情绪/主线/框架都已重建，链的最终目的达成。
    链无记录/被杀/中止在框架前/框架真失败 → False。
    """
    ch = (doc.get("chains") or {}).get(chain) or {}
    return any(s.get("name") == "framework" and s.get("ok")
               for s in ch.get("steps", []))


def watchdog_decide(day: str, doc: dict, cfg: dict) -> list[dict]:
    """18:00 守夜判定（**唯一告警出口**）。返回 [{chain, level, content}]；空=静默。

    2026-10-31 用户拍板"只推不能正常运行"：
    - 数据链任一条把 framework 跑成功 → 一切静默（partial_failed/单链缺席/
      恢复知会全取消——兜底救回来了就是正常运行，不打扰）；
    - 两条链都无 framework ok → 严重（缺席/被杀/中止在框架前/框架失败）；
    - 复盘推送未完成且无成功推送落档 → 严重（推送不能正常运行）。
    非交易日静默（原有）。
    """
    if not _is_trading_day(cfg, day):  # 本模块加固版：日历陈旧时按交易日处理
        return []
    out = []
    if not (_rebuilt_ok(doc, "market-1600") or _rebuilt_ok(doc, "market-1745")):
        o1600 = chain_outcome(doc, "market-1600")
        if not already_alerted(doc, "market-1600"):
            sev = ("缺席/被终止" if o1600 in ("none", "running") else "硬失败")
            msg = build_failure_message(day, "market-1600", doc, severe=True)
            msg += f"\n**状态**：{sev}，17:45 兜底亦未完成当日重建"
            out.append({"chain": "market-1600", "level": "severe", "content": msg})
    opush = chain_outcome(doc, "review-push-1700")
    if (opush in ("none", "running", "partial_failed")
            and not _review_pushed_today(day)
            and not already_alerted(doc, "review-push-1700")):
        out.append({"chain": "review-push-1700", "level": "severe",
                    "content": f"**级别**：严重\n**日期**：{day}\n"
                               f"**主机**：{doc.get('host','')}\n"
                               f"**任务**：17:00 复盘推送未完成（链状态：{opush}），"
                               f"且无成功推送落档"})
    return out


def cmd_watchdog() -> int:
    from datetime import date
    day = date.today().isoformat()
    doc = load_status(day)
    for a in watchdog_decide(day, doc, load_config()):
        send_alert(day, a["chain"], a["content"])
    return 0


# ---------------- 子命令入口 ----------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="astock_hub.chain_guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("beat")
    b.add_argument("--chain", required=True)
    b.add_argument("--step", required=True)
    b.add_argument("--fail", action="store_true")
    b.add_argument("--skipped", action="store_true",
                   help="有意跳过/未就绪（非故障，不计入失败）")
    b.add_argument("--tail", default="")
    f = sub.add_parser("finalize")
    f.add_argument("--chain", required=True)
    f.add_argument("--trading-day", dest="trading_day",
                   choices=["yes", "no"], default=None,
                   help="覆盖交易日判定（非交易日不发即时告警）")
    sub.add_parser("watchdog")
    sub.add_parser("test")
    a = ap.parse_args(argv)
    from datetime import date
    day = date.today().isoformat()
    try:
        if a.cmd == "beat":
            record_beat(day, a.chain, a.step, not a.fail, a.tail,
                        skipped=a.skipped)
        elif a.cmd == "finalize":
            td = None if a.trading_day is None else (a.trading_day == "yes")
            finalize(day, a.chain, trading_day=td)
        elif a.cmd == "watchdog":
            return cmd_watchdog()
        elif a.cmd == "test":
            ok = send_alert(day, "test", "**级别**：测试\n这是一条告警通道测试消息。")
            print("sent" if ok else "skipped（未启用或地址为空）")
    except Exception as e:  # noqa: BLE001 — 铁律：不影响数据链
        print(f"chain_guard 自身异常（已忽略）：{e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
