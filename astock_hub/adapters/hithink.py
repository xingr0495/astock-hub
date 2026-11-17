"""hithink-finance CLI 适配器（subprocess 薄封装，同花顺金融数据服务）。

定位：行情维度的第二数据源——tickflow 挂掉时补位，并与 tickflow 交叉验证。
能力边界：无分钟K/tick/Level-2（逐笔资金数据归 a-stock-data 技能）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime, time as dtime, timedelta, timezone

from .. import errors, models
from .base import Adapter, timed

_CST = timezone(timedelta(hours=8))  # Asia/Shanghai


def _day_ms(d: date, end_of_day: bool = False) -> int:
    t = dtime(23, 59, 59) if end_of_day else dtime(0, 0, 0)
    return int(datetime.combine(d, t, tzinfo=_CST).timestamp() * 1000)


def _ms_to_date(ms: int | float | None) -> str | None:
    """行情毫秒时间戳 → 北京日期的 ISO 字符串（hithink 日线只给 date_ms，不给你 date）。"""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=_CST).date().isoformat()
    except (ValueError, TypeError, OSError):
        return None


class HithinkAdapter(Adapter):
    name = "hithink"
    source = "hithink-cli"
    freshness_kind = "daily"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.cli = cfg.get("cli", "hithink-finance")
        self.timeout = int(cfg.get("timeout_sec", 30))

    def _run(self, args: list[str], timeout: int | None = None) -> tuple[dict, str]:
        """执行 CLI 并解析结构化信封。失败抛 RuntimeError(错误对象)。"""
        exe = shutil.which(self.cli)
        if not exe:
            raise RuntimeError(errors.make_error(
                errors.CLI_MISSING, f"PATH 中找不到 {self.cli}",
                "hithink-finance CLI 未安装。参考用户级技能 hithink-finance 的安装指引，"
                "或在 https://fuyao.aicubes.cn/admin 获取 API Key 后安装官方 CLI"))
        timeout = timeout or self.timeout
        cmd_str = f"{self.cli} {' '.join(args)}"
        try:
            proc = subprocess.run(
                [exe, *args, "--format", "json"],
                capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                # stdin 置空：防止 CLI 在父进程 stdin 是管道时阻塞等待输入
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(errors.make_error(
                errors.TIMEOUT, f"命令超时（{timeout}s）：{cmd_str}",
                "远端服务可能繁忙，稍后重试", retryable=True))
        try:
            envelope = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(errors.make_error(
                errors.PARSE_ERROR,
                f"CLI 输出不是有效 JSON（退出码 {proc.returncode}）",
                hint=(proc.stderr or "").strip()[:300] or None))
        if not envelope.get("ok"):
            raise RuntimeError(errors.make_error(
                errors.UPSTREAM_ERROR,
                f"CLI 返回失败：{json.dumps(envelope.get('error') or envelope, ensure_ascii=False)[:300]}",
                "常见原因：API Key 未配置或失效（检查 HITHINK_FINANCE_API_KEY 环境变量），"
                "或参数/标的超出能力边界（无分钟K/tick/Level-2）",
                retryable=True))
        return envelope, cmd_str

    def health(self) -> models.DimensionResult:
        try:
            with timed() as t:
                envelope, cmd = self._run(["version"])
            return self.ok(envelope.get("data"), endpoint=cmd, latency_ms=t["ms"],
                           kind="realtime")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """名称/代码 → thscode 候选列表。失败返回空列表（搜索是辅助手段，不报错）。"""
        try:
            envelope, _ = self._run(["symbol", "search", "--q", query,
                                     "--asset-type", "a-share", "--limit", str(limit)])
            return (envelope.get("data") or {}).get("item") or []
        except RuntimeError:
            return []

    def snapshot(self, thscodes: list[str]) -> models.DimensionResult:
        """实时/最新行情快照。"""
        try:
            with timed() as t:
                envelope, cmd = self._run(
                    ["market", "snapshot", "--thscodes", ",".join(thscodes)])
            data = envelope.get("data") or {}
            ts = data.get("timestamp")
            as_of = (datetime.fromtimestamp(ts / 1000, tz=_CST).date().isoformat()
                     if ts else None)
            return self.ok(data, as_of=as_of, endpoint=cmd, latency_ms=t["ms"],
                           kind="realtime")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def history(self, thscode: str, days: int = 120, tail: int = 60,
                adjust: str = "forward") -> models.DimensionResult:
        """日K历史（默认前复权）。为控制体积只保留尾部 tail 行。

        `days` 是想要的**交易日**数（内部按 1.6 倍换成自然日窗口）；
        `adjust` 取 forward（前复权）/ none（原始价，算涨跌停要用原始价）。
        `tail` 给成与 days 相同即为不裁剪（指标计算需要足够的预热行）。
        """
        end = date.today()
        start = end - timedelta(days=int(days * 1.6) + 10)  # 留出非交易日余量
        try:
            with timed() as t:
                envelope, cmd = self._run([
                    "market", "history", "--thscode", thscode,
                    "--start-ms", str(_day_ms(start)),
                    "--end-ms", str(_day_ms(end, end_of_day=True)),
                    "--adjust", adjust,
                ])
            data = envelope.get("data") or {}
            items = data.get("item") or data.get("rows") or []
            if isinstance(items, list) and len(items) > tail:
                data = dict(data)
                key = "item" if "item" in data else "rows"
                data[key] = items[-tail:]
                data["_note"] = f"共 {len(items)} 行，仅保留尾部 {tail} 行"
            as_of = _ms_to_date(items[-1].get("date_ms")) if items else None
            return self.ok(data, as_of=as_of, endpoint=cmd, latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def db_export(self, sql: str, out_path: str, *, file_format: str = "parquet",
                  timeout: int = 1200) -> models.DimensionResult:
        """把只读 SQL 结果导出到文件（大结果必须落盘，只回报路径）。

        这是读全市场历史的正路：库里有 `v_daily`（不复权原始价）与 `v_daily_qfq`
        （前复权）两个视图，按需选。走 CLI 而不是自己 attach 那个 .duckdb，
        因为 CLI 会正确处理它自己的数据锁与 schema 版本。
        """
        try:
            with timed() as t:
                envelope, cmd = self._run(
                    ["db", "export", "--sql", sql, "--output", out_path,
                     "--file-format", file_format], timeout=timeout)
            data = dict(envelope.get("data") or {})
            data.setdefault("path", out_path)
            return self.ok(data, endpoint=cmd, latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def index_history(self, thscode: str, start: date, end: date) -> models.DimensionResult:
        """指数日线历史（用于情绪周期的「指数涨幅」维度）。"""
        try:
            with timed() as t:
                envelope, cmd = self._run([
                    "index", "history", "--thscode", thscode,
                    "--start-ms", str(_day_ms(start)),
                    "--end-ms", str(_day_ms(end, end_of_day=True)),
                ])
            data = envelope.get("data") or {}
            items = data.get("item") or []
            as_of = _ms_to_date(items[-1].get("date_ms")) if items else None
            return self.ok(data, as_of=as_of, endpoint=cmd, latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def realtime_snapshot(self, thscodes: list[str]) -> models.DimensionResult:
        """A 股实时行情快照（market snapshot，同花顺同源）。

        盘中是实时价、盘后是收盘价。这是解决"跟同花顺软件对不上"的实时来源：
        逐股返回 last_price/涨跌幅/开高低/量额。一次建议 ≤100 只。
        """
        try:
            with timed() as t:
                envelope, cmd = self._run([
                    "market", "snapshot",
                    "--thscodes", ",".join(thscodes)])
                data = envelope.get("data") or {}
                ts_ms = data.get("timestamp")
                meta_note = None
                if ts_ms:
                    from datetime import datetime as _dt, timezone as _tz
                    meta_note = _dt.fromtimestamp(
                        int(ts_ms) / 1000, tz=_tz.utc).isoformat()
                return self.ok(data, endpoint=cmd, latency_ms=t["ms"],
                               note=f"实时快照 ts={meta_note}")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def data_status(self) -> models.DimensionResult:
        """本地库状态。`version=0` / 行数为 0 表示还没 init 或 sync。"""
        try:
            with timed() as t:
                envelope, cmd = self._run(["data", "status"])
            return self.ok(envelope.get("data") or {}, endpoint=cmd, latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def data_panel(self, start: str, end: str, out_path: str,
                   timeout: int = 900) -> models.DimensionResult:
        """导出全市场区间面板到 parquet（供情绪周期/主线等全市场批量分析读）。

        CLI 契约要求大结果必须落盘，所以这里只回报路径、行数、窗口，不把数据塞进
        上下文。`start`/`end` 用 YYYY-MM-DD。
        """
        try:
            with timed() as t:
                envelope, cmd = self._run(
                    ["market", "panel", "--start", start, "--end", end,
                     "--output", out_path], timeout=timeout)
            data = dict(envelope.get("data") or {})
            data.setdefault("path", out_path)
            data.setdefault("start", start)
            data.setdefault("end", end)
            return self.ok(data, as_of=end, endpoint=cmd, latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def calendar(self) -> models.DimensionResult:
        """A 股交易日历（远端，单次覆盖约一年）。"""
        try:
            with timed() as t:
                envelope, cmd = self._run(["market", "calendar"])
            data = envelope.get("data") or {}
            items = data.get("item") or []
            as_of = items[-1].get("date") if items else None
            if as_of and len(as_of) == 8:  # CLI 用的是 YYYYMMDD 紧凑格式
                as_of = f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}"
            return self.ok(data, as_of=as_of, endpoint=cmd, latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def limit_pool(self, kind: str = "up", size: int = 20) -> models.DimensionResult:
        """涨停/跌停池（kind: up / down）。需要 --date-ms 时用最近一天。"""
        command = {"up": "limit-up-pool", "down": "limit-down-pool"}[kind]
        date_ms = _day_ms(date.today())
        try:
            with timed() as t:
                envelope, cmd = self._run([
                    "special", command, "--date-ms", str(date_ms), "--size", str(size)])
            data = envelope.get("data") or {}
            return self.ok(data, as_of=date.today().isoformat(), endpoint=cmd,
                           latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def limit_ladder(self) -> models.DimensionResult:
        """连板梯队（几连板各有哪些股票，判断短线情绪高度）。"""
        try:
            with timed() as t:
                envelope, cmd = self._run(["special", "limit-up-ladder"])
            data = envelope.get("data") or {}
            return self.ok(data, as_of=date.today().isoformat(), endpoint=cmd,
                           latency_ms=t["ms"])
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def index_catalog(self, tag: str = "cn_concept") -> list[dict]:
        """概念目录（cn_concept 约 390 条）。失败返回空列表（辅助数据，不报错）。"""
        try:
            envelope, _ = self._run(["index", "catalog", "--tag", tag])
            return (envelope.get("data") or {}).get("item") or []
        except RuntimeError:
            return []

    def symbol_list(self, offset: int = 0, limit: int = 1000) -> models.DimensionResult:
        """全 A 股标的清单（symbol list，翻页、一次 ≤10000）。"""
        try:
            with timed() as t:
                envelope, cmd = self._run(
                    ["symbol", "list", "--asset-type", "a-share",
                     "--limit", str(min(limit, 10000)), "--offset", str(offset)])
            items = (envelope.get("data") or {}).get("item") or []
            return self.ok({"item": items, "total": len(items), "offset": offset},
                           endpoint=cmd, latency_ms=t["ms"],
                           note=f"hithink 标的清单第 {offset} 页 {len(items)} 条")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def constituents(self, thscode: str) -> models.DimensionResult:
        """指数/概念成分股（index constituents，一次一个指数，无数量上限）。"""
        try:
            with timed() as t:
                envelope, cmd = self._run(["index", "constituents", "--thscode", thscode])
            items = (envelope.get("data") or {}).get("item") or []
            return self.ok({"item": items, "total": len(items)},
                           as_of=date.today().isoformat(), endpoint=cmd,
                           latency_ms=t["ms"], note=f"{thscode} 成分 {len(items)} 条")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)

    def valuation_thscodes(self, thscodes: list[str]) -> models.DimensionResult:
        """批量估值快照（PE/PB/PS）。接口硬上限 100 只/次，内部自动分片。
        注意：hithink 快照不含股票名，命名一律以 tickflow 全市场表为准。"""
        items: list[dict] = []
        first_cmd = None
        total_ms = 0
        try:
            for i in range(0, len(thscodes), 100):
                chunk = thscodes[i:i + 100]
                with timed() as t:
                    envelope, cmd = self._run(
                        ["valuation", "snapshot", "--thscodes", ",".join(chunk)])
                first_cmd = first_cmd or cmd
                total_ms += t["ms"]
                items.extend((envelope.get("data") or {}).get("item") or [])
            return self.ok({"item": items, "total": len(items)},
                           as_of=date.today().isoformat(), endpoint=first_cmd,
                           latency_ms=total_ms, note=f"估值 {len(thscodes)} 只 / {len(items)} 只返回")
        except RuntimeError as e:
            err = e.args[0]
            return self.fail(err["code"], err["message"], err.get("hint"),
                             err.get("retryable", False), endpoint=self.cli)
