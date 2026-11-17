"""ifind-finance-data（同花顺 iFinD MCP）适配器。

数据面第三路：为选股管道提供"同花顺官方"的行情/估值快照与智能选股。
实现方式：复用用户已装技能目录里的 call-node.js（MCP JSON-RPC 客户端），
以子进程方式调用，天然支持批量行情（一次 ≤10 只）与自然语言智能选股。

限制与坑：
- 密钥在技能目录 mcp_config.json 的 auth_token（官网个人中心获取），
  未配置时返回 unavailable + 指引（不炸管道）
- 免费层并发上限 2 请求/秒 -> 适配器进程级闸门限流（最小间隔 0.55s）
- 返回数据是「JSON 包 JSON 包 markdown/表格」的嵌套结构，解析失败返回空
  并记 note，不抛异常
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import errors, models
from .base import Adapter, timed

# 免费层并发 2 请求/秒，取安全间隔
_MIN_INTERVAL = 0.55
_rate_lock = threading.Lock()
_last_request_at = 0.0

# 实时快照指标（≤10 个）：行情软件同花顺口径，含估值/市值/开高低
_QUOTE_INDICATORS = (
    "最新价,涨跌幅,换手率,量比,总市值,市盈率TTM,市净率,开盘价,最高价,最低价"
)

_RUNNER = r"""
const { call } = require('%SKILL%call-node.js');
const args = JSON.parse(process.env.IFIND_ARG || '{}');
(async () => {
  try {
    const r = await call(args.server, args.tool, args.params || {});
    console.log('IFINDRESULT' + JSON.stringify(r));
  } catch (e) {
    console.error('IFINDERR' + JSON.stringify({ message: String((e && e.message) || e) }));
    process.exit(1);
  }
})();
"""


def _sleep_rate_limited() -> None:
    global _last_request_at
    with _rate_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()


def _parse_call_payload(text: str) -> dict | None:
    """解开 MCP content.text 的嵌套 JSON（text -> dict，其 data 字段又是 JSON 串）。"""
    try:
        outer = json.loads(text)
        if outer.get("code") not in (None, 0, 1):
            return None
        data = outer.get("data")
        if isinstance(data, str):
            return json.loads(data)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        return None
    return None


def _parse_md_table(answer: str) -> list[dict]:
    """markdown 表格 -> [{col: value}]。首行表头，第二行分隔线，之后数据行。"""
    lines = [ln for ln in answer.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # 分隔线行
        row = {}
        for i, col in enumerate(header):
            row[col] = cells[i].strip() if i < len(cells) else ""
        rows.append(row)
    return rows


class IfindAdapter(Adapter):
    name = "ifind"
    source = "ifind-mcp"
    freshness_kind = "realtime"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.skill_dir = Path(
            cfg.get("skill_dir")
            or str(Path.home() / ".claude" / "skills"
                   / "ifind-finance-data-1.4.0"))
        self.node = cfg.get("node", "node")
        self.timeout = int(cfg.get("timeout_sec", 90))

    # ---------- 基础设施 ----------

    def _key_ready(self) -> tuple[bool, str]:
        cfg_path = self.skill_dir / "mcp_config.json"
        try:
            tok = (json.loads(cfg_path.read_text(encoding="utf-8")) or {}).get("auth_token", "") or ""
        except (OSError, ValueError):
            return False, f"找不到技能配置 {cfg_path}"
        if not tok or "your ifind" in tok.lower().replace(" ", ""):
            return False, f"{cfg_path} 里还是占位密钥：请到 iFinD MCP 官网个人中心获取密钥后填进去"
        return True, ""

    def _call(self, server_type: str, tool_name: str, params: dict) -> dict | None:
        ready, why = self._key_ready()
        if not ready:
            raise RuntimeError((errors.CONFIG_ERROR, why))
        # %SKILL% -> JS 字符串字面量（repr 处理好反斜杠转义）：
        #  require('C:\\Users\\...\\call-node.js')
        runner = _RUNNER.replace("%SKILL%", repr(f"{self.skill_dir}\\")[1:-1])
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "ifind_run.js"
            script.write_text(runner, encoding="utf-8")
            env = dict(os.environ)
            env["IFIND_ARG"] = json.dumps(
                {"server": server_type, "tool": tool_name, "params": params},
                ensure_ascii=False)
            _sleep_rate_limited()
            proc = subprocess.run(
                [self.node, str(script)], capture_output=True,
                timeout=self.timeout, env=env,
                encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL)
        stdout = proc.stdout or ""
        if "IFINDERR" in stdout:
            msg = stdout.split("IFINDERR", 1)[1].strip()
            try:
                raise RuntimeError((errors.UPSTREAM_ERROR, json.loads(msg).get("message", msg)))
            except ValueError:
                raise RuntimeError((errors.UPSTREAM_ERROR, msg))
        if "IFINDRESULT" not in stdout:
            raise RuntimeError((errors.PARSE_ERROR, f"ifind 调用无输出（退出码 {proc.returncode}）"))
        raw = stdout.split("IFINDRESULT", 1)[1].strip()
        try:
            result = json.loads(raw)
        except ValueError:
            raise RuntimeError((errors.PARSE_ERROR, "ifind 返回不是有效 JSON"))
        if not result.get("ok"):
            raise RuntimeError((errors.UPSTREAM_ERROR,
                                json.dumps(result.get("error"), ensure_ascii=False)))
        content = (result.get("data") or {}).get("result") or {}
        items = (content.get("content") or [])
        payload = None
        for item in items:
            if item.get("type") == "text" and (item.get("text") or "").strip():
                payload = _parse_call_payload(item["text"])
                if payload is not None:
                    break
        return payload

    def health(self) -> models.DimensionResult:
        ready, why = self._key_ready()
        if not ready:
            return self.fail(errors.CONFIG_ERROR, "ifind 密钥未配置", why, endpoint=str(self.skill_dir))
        try:
            payload = self._call("index", "index_highfreq_quotes", {
                "symbols": "000001.SH", "indicators": "最新价",
                "data_mode": "real_time"})
            if payload is None:
                raise RuntimeError((errors.UPSTREAM_ERROR, "返回为空"))
            return self.ok({"ping": "ok"}, endpoint="ifind-mcp", kind="realtime")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], retryable=True, endpoint="ifind-mcp")

    # ---------- 业务方法 ----------

    def quotes(self, thscodes: list[str]) -> models.DimensionResult:
        """实时行情快照（一次 ≤10 只，自动分片），含估值/市值/换手/量比。

        返回 data: {"item":[{symbol,name,time,price,change_pct,turnover_ratio,
        vol_ratio,total_capital,float_capital,pe_ttm,pb}], "total": n}
        change_pct 已是百分数（ifind 口径）。
        """
        items: list[dict] = []
        as_of = None
        try:
            for i in range(0, len(thscodes), 10):
                chunk = thscodes[i:i + 10]
                with timed() as t:
                    payload = self._call("stock", "stock_highfreq_quotes", {
                        "symbols": ",".join(chunk),
                        "indicators": _QUOTE_INDICATORS,
                        "data_mode": "real_time"})
                table = (payload or {}).get("tables") or []
                if len(table) >= 2:
                    header = [str(h).strip() for h in table[0]]
                    idx = {name: position for position, name in enumerate(header)}
                    for row in table[1:]:
                        def val(col: str):
                            pos = idx.get(col)
                            return row[pos] if pos is not None and pos < len(row) else None
                        items.append({
                            "symbol": str(val("证券代码") or "").strip(),
                            "name": str(val("证券简称") or "").strip(),
                            "time": val("time"),
                            "price": _f(val("最新价")),
                            "change_pct": _f(val("涨跌幅")),
                            "turnover_ratio": _f(val("换手率")),
                            "vol_ratio": _f(val("量比")),
                            "total_capital": _f(val("总市值")),
                            "pe_ttm": _f(val("市盈率TTM")),
                            "pb": _f(val("市净率")),
                            "open": _f(val("开盘价")),
                            "high": _f(val("最高价")),
                            "low": _f(val("最低价")),
                        })
                    if items and as_of is None:
                        as_of = (items[0].get("time") or "")[:10]
                        as_of = as_of or None
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], err[2] if len(err) > 2 else None,
                             retryable=True, endpoint="ifind-mcp")
        if not items:
            return self.ok({"item": [], "total": 0}, as_of=as_of,
                           note="ifind 实时行情无返回（可能非实时行情时段）")
        return self.ok({"item": items, "total": len(items)},
                       as_of=as_of, endpoint="ifind-mcp",
                       note=f"ifind 实时快照 {len(items)} 只，含估值/市值/换手/量比")

    def market_breadth(self) -> models.DimensionResult:
        """市场宽度（同花顺口径，含北交所，与行情软件一致）。

        - 上涨/下跌家数：iFinD 智能选股 按"涨跌幅>0 / <0"统计的全市场选中数
          （实测与同花顺 APP 收盘数据一致：9/18 上涨4234/下跌1152）
        - 涨停/跌停/停牌家数与成交额：上证(+科创)+深证(+创业) 指数实时快照求和
          （沪深合计）
        返回 data: {"scope":"全市场","up","down","limit_up","limit_down",
        "suspended","amount","per_index":[...],"count_source":"search|index"}
        """
        # 1) 智能选股统计全市场涨跌家数（失败回退指数求和口径）
        up = down = None
        count_source = "index"
        answer_text = ""
        try:
            p_up = self._call("stock", "search_stocks",
                              {"query": "今日A股涨跌幅大于0的股票"})
            p_dn = self._call("stock", "search_stocks",
                              {"query": "今日A股涨跌幅小于0的股票"})
            answer_text = str((p_up or {}).get("answer") or "")
            up = int((p_up or {}).get("selectedSecuritiesCount") or 0) or None
            down = int((p_dn or {}).get("selectedSecuritiesCount") or 0) or None
            count_source = "search"
        except RuntimeError:
            pass

        # 2) 指数求和：涨停/跌停/停牌/成交额（沪深）
        INDS = ["000001.SH", "399001.SZ"]
        cols_want = ("上涨家数", "下跌家数", "涨停家数", "跌停家数", "停牌家数", "成交额")
        per: list[dict] = []
        total: dict[str, float] = {}
        try:
            payload = self._call("index", "index_highfreq_quotes", {
                "symbols": ",".join(INDS),
                "indicators": ",".join(cols_want),
                "data_mode": "real_time"})
            table = (payload or {}).get("tables") or []
            if len(table) >= 2:
                header = [str(h).strip() for h in table[0]]
                idx = {name: position for position, name in enumerate(header)}
                for row in table[1:]:
                    def val(col: str):
                        pos = idx.get(col)
                        return row[pos] if pos is not None and pos < len(row) else None
                    rec = {
                        "symbol": str(val("证券代码") or "").strip(),
                        "name": str(val("证券简称") or "").strip(),
                    }
                    for c in cols_want:
                        v = _f(val(c))
                        rec[c] = v
                        if v is not None:
                            total.setdefault(c, 0.0)
                            total[c] = total[c] + v
                    per.append(rec)
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], err[2] if len(err) > 2 else None,
                             retryable=True, endpoint="ifind-mcp")

        base = {
            "scope": "全市场(沪深京)",
            "up": up,
            "down": down,
            "limit_up": int(total.get("涨停家数", 0)) if total else None,
            "limit_down": int(total.get("跌停家数", 0)) if total else None,
            "suspended": int(total.get("停牌家数", 0)) if total else None,
            "amount": total.get("成交额"),
            "per_index": per,
            "count_source": count_source,
        }
        # 智能选股计数失败时，用指数求和兜底涨跌家数
        if up is None and "上涨家数" in total:
            base["up"] = int(total["上涨家数"])
            base["down"] = int(total.get("下跌家数", 0))
        # 两路全空 = 上游无数据（最常见：MCP 额度耗尽，answer 返回"用量已耗尽"文案）。
        # 如实失败而不是 ok+None——ok+None 会让 reconcile 把"参考源没数据"误判成全项 MISMATCH。
        if base["up"] is None and base["amount"] is None and base["limit_up"] is None:
            exhausted = "耗尽" in answer_text or "订阅" in answer_text or "权益" in answer_text
            return self.fail(
                "UPSTREAM_EMPTY" if not exhausted else "QUOTA_EXHAUSTED",
                "iFinD 市场宽度无数据：" + (answer_text[:160] or "智能选股与指数求和均为空"),
                hint=("iFinD MCP 额度耗尽：到 mcp.51ifind.com 充值/升级或加购增量包"
                      if exhausted else "iFinD 返回空，稍后重试或检查凭证"),
                retryable=True, endpoint="ifind-mcp")
        return self.ok(base, as_of=None, endpoint="ifind-mcp",
                       note=f"iFinD 市场宽度：智能选股统计({'成功' if count_source=='search' else '回退指数求和'})；"
                            f"涨停/跌停/成交为沪深合计")

    def kline_nlast(self, code: str, days: int = 60) -> models.DimensionResult:
        """近 N 个交易日不复权收盘序列（iFinD get_stock_performance，自然语言）。

        返回 data: {"symbol","name","total_rows","rows":[{date,close,change_pct}]}
        date 归一化为 YYYY-MM-DD；前后复权由 iFinD 按"不复权"指标返回。
        """
        days = min(max(int(days), 10), 250)
        try:
            payload = self._call("stock", "get_stock_performance",
                                 {"query": f"{code} 最近{days}个交易日的收盘价和涨跌幅"})
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], err[2] if len(err) > 2 else None,
                             retryable=True, endpoint="ifind-mcp")
        rows0 = _parse_md_table((payload or {}).get("answer") or "")
        if not rows0:
            return self.fail(errors.UPSTREAM_ERROR,
                             f"iFinD 未返回 {code} 的日K序列",
                             "可换股票代码（6位）重试，或检查指标写法", retryable=True)
        cols = rows0[0]
        date_col = next((c for c in cols if "日期" in c or c == "date"), None)
        close_col = next((c for c in cols if "收盘价" in c), None)
        pct_col = next((c for c in cols if c.startswith("涨跌幅")), None)
        rows = []
        symbol = name = None
        for r in rows0:
            raw_date = str(r.get(date_col) or "").strip()
            if len(raw_date) == 8 and raw_date.isdigit():
                raw_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            close = _f(r.get(close_col))
            if raw_date and close is not None:
                rows.append({
                    "date": raw_date,
                    "close": close,
                    "change_pct": _f(r.get(pct_col)),
                })
            if not symbol and r.get("证券代码"):
                symbol = r["证券代码"]
            if not name and r.get("证券简称"):
                name = r["证券简称"]
        rows.reverse()  # iFinD 按日期倒序返回，翻成升序（最新的在最后）
        if not rows:
            return self.fail(errors.UPSTREAM_ERROR, f"iFinD 日K解析为空（{code}）",
                             "iFinD 对个别次新股/停牌股可能无序列", retryable=True)
        return self.ok({
            "symbol": symbol or code, "name": name,
            "total_rows": len(rows),
            "source": "ifind(不复权)",
            "rows": rows,
        }, as_of=rows[-1]["date"], endpoint="ifind-mcp",
            note=f"iFinD 不吃权日K，近 {len(rows)} 个交易日收盘序列")

    def limit_pool(self, direction: str = "up") -> models.DimensionResult:
        """涨停/跌停池（iFinD 智能选股自然语言）。

        direction: up / down。返回 data: {"direction","count","rows":[{symbol,name}],
        "answer"}；count 优先用服务端 selectedSecuritiesCount，缺省按行数。
        """
        q = "今日A股涨停的股票" if direction == "up" else "今日A股跌停的股票"
        try:
            payload = self._call("stock", "search_stocks", {"query": q})
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], err[2] if len(err) > 2 else None,
                             retryable=True, endpoint="ifind-mcp")
        rows = [{"symbol": r.get("股票代码") or "", "name": r.get("股票简称") or ""}
                for r in _parse_md_table((payload or {}).get("answer") or "")]
        rows = [r for r in rows if r["symbol"]]
        count = int((payload or {}).get("selectedSecuritiesCount") or 0) or len(rows)
        return self.ok({"direction": direction, "count": count, "rows": rows,
                        "answer": ((payload or {}).get("answer") or "")[:800]},
                       as_of=None, endpoint="ifind-mcp",
                       note=f"iFinD {('涨停' if direction=='up' else '跌停')}池 {count} 只")

    def index_quotes(self, thscodes: list[str]) -> models.DimensionResult:
        """指数实时行情快照（index_highfreq_quotes，一次 ≤10 个）。

        返回 data: {"item":[{symbol,name,time,price,change_pct,amount,
        open,high,low}], "total": n}；change_pct 为百分数。
        """
        items: list[dict] = []
        as_of = None
        try:
            for i in range(0, len(thscodes), 10):
                chunk = thscodes[i:i + 10]
                payload = self._call("index", "index_highfreq_quotes", {
                    "symbols": ",".join(chunk),
                    "indicators": "最新价,涨跌幅,成交额,开盘价,最高价,最低价",
                    "data_mode": "real_time"})
                table = (payload or {}).get("tables") or []
                if len(table) >= 2:
                    header = [str(h).strip() for h in table[0]]
                    idx = {name: position for position, name in enumerate(header)}
                    for row in table[1:]:
                        def val(col: str):
                            pos = idx.get(col)
                            return row[pos] if pos is not None and pos < len(row) else None
                        items.append({
                            "symbol": str(val("证券代码") or "").strip(),
                            "name": str(val("证券简称") or "").strip(),
                            "time": val("time"),
                            "price": _f(val("最新价")),
                            "change_pct": _f(val("涨跌幅")),
                            "amount": _f(val("成交额")),
                            "open": _f(val("开盘价")),
                            "high": _f(val("最高价")),
                            "low": _f(val("最低价")),
                        })
                    if items and as_of is None:
                        as_of = (items[0].get("time") or "")[:10] or None
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], err[2] if len(err) > 2 else None,
                             retryable=True, endpoint="ifind-mcp")
        if not items:
            return self.ok({"item": [], "total": 0}, as_of=as_of,
                           note="ifind 指数行情无返回")
        return self.ok({"item": items, "total": len(items)},
                       as_of=as_of, endpoint="ifind-mcp",
                       note=f"ifind 指数实时快照 {len(items)} 个")

    def smart_screen(self, query: str) -> models.DimensionResult:
        """自然语言智能选股。返回 data: {"query", "answer", "rows":[{...}]}。

        rows 是从 markdown 表拆出的行（列名即表头），代码列找"股票代码/证券代码"。
        """
        try:
            payload = self._call("stock", "search_stocks", {"query": query})
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err[0], err[1], err[2] if len(err) > 2 else None,
                             retryable=True, endpoint="ifind-mcp")
        answer = (payload or {}).get("answer") or ""
        rows = _parse_md_table(answer)
        code_col = next((c for c in ("股票代码", "证券代码", "代码") if c in (rows[0] if rows else {})), None)
        return self.ok({"query": query, "answer": answer[:2000], "rows": rows,
                        "code_col": code_col},
                       as_of=None, endpoint="ifind-mcp",
                       note=f"ifind 智能选股返回 {len(rows)} 行")


def _f(v):
    """安全转 float：'null'/None/'-' 等返回 None。"""
    if v in (None, "", "null", "-", "--"):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None