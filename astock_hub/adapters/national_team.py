"""国家队资金适配器：透视系统 PostgreSQL（个股持仓结论）+ 持仓估计 JSON（宽基 ETF 份额）。

- PG 结论宽表 nt_positions_analysis（Dashboard 同口径），数据库层强制只读
- ETF JSON 是 GBK 编码（硬坑：按 utf-8 读会直接炸）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .. import errors, models
from ..config import PROJECT_ROOT
from .base import Adapter, timed

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

_POSITIONS_SQL = """
SELECT ts_code, name, holder_name, period_end, hold_amount, est_cost,
       curr_price, profit_rate, status, cost_source, first_buy_date,
       change_analysis, update_time
FROM nt_positions_analysis
WHERE is_latest = true AND ts_code = %s
ORDER BY holder_name
"""

_CHANGES_SQL = """
SELECT holder_name, change_analysis, COUNT(*) AS cnt
FROM nt_positions_analysis
WHERE is_latest = true AND period_end = (SELECT MAX(period_end) FROM nt_positions_analysis)
  AND change_analysis IS NOT NULL AND change_analysis <> ''
GROUP BY holder_name, change_analysis
ORDER BY cnt DESC
LIMIT %s
"""

# holder_name 文本 → 机构类别（表里没有 holder_type 列）
_HOLDER_CLASSES = [
    ("汇金", "中央汇金"),
    ("证金", "中国证券金融"),
    ("社保", "社保基金"),
    ("养老", "基本养老保险"),
    ("国新", "国新投资"),
    ("集成电路", "国家集成电路基金"),
]


def _classify_holder(holder_name: str) -> str:
    for keyword, label in _HOLDER_CLASSES:
        if keyword in holder_name:
            return label
    return "其他"


def _wow_vs_days_ago(series: list, latest: dict, days: int = 7) -> float | None:
    """日频序列周环比：最新 vs days 个自然日前（或之前最近）一条的合计差。"""
    def _d(e):
        s = str(e.get("date") or "")
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None

    ld = _d(latest)
    if ld is None:
        return None
    target = ld - timedelta(days=days)
    for e in reversed(series[:-1]):
        ed = _d(e)
        if ed is not None and ed <= target and e.get("全宽基合计"):
            return round(latest["全宽基合计"] - e["全宽基合计"], 2)
    return None


class NationalTeamAdapter(Adapter):
    name = "national_team"
    source = "national-team-pg"
    freshness_kind = "quarterly"  # 股东持仓按财报期披露

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.dsn = cfg.get("dsn")
        self.etf_json_path = cfg.get("etf_json_path")
        self.etf_json_daily_path = cfg.get("etf_json_daily_path")
        self.enabled = cfg.get("enabled", True)

    def _disabled(self) -> models.DimensionResult:
        return models.DimensionResult(
            status=models.SKIPPED, source=self.source,
            meta=models.SourceMeta(note="国家队持仓透视系统已停用"),
            data={"note": "国家队持仓透视系统（旧国家队持仓透视系统（ETL+PG））已停用"
                          "（2026-09-29 起，ETL 维护成本高：tushare 限流/东财风控）。"
                          "国家队维度改用「国家队持仓估计」的宽基 ETF 份额（astock_nt_etf_shares）。"
                          "重新启用：config.toml [national_team] enabled = true，"
                          "并先跑 ETL + analysis_engine 刷新数据"})

    def _missing_dep(self) -> models.DimensionResult | None:
        if psycopg2 is None:
            return self.fail(
                errors.DEPENDENCY_MISSING, "未安装 psycopg2",
                "在 astock-hub 的虚拟环境里运行 setup.ps1，或手动执行 "
                ".venv\\Scripts\\python.exe -m pip install psycopg2-binary")
        if not self.dsn:
            return self.fail(errors.NOT_CONFIGURED, "config.toml 缺少 [national_team].dsn")
        return None

    def _connect(self):
        return psycopg2.connect(
            self.dsn, connect_timeout=3,
            options="-c default_transaction_read_only=on")

    def _pg_fail(self, exc: Exception) -> models.DimensionResult:
        return self.fail(
            errors.CONNECTION_REFUSED, f"PostgreSQL 连接失败：{exc}",
            "国家队数据库未启动或连接串不对。检查 config.toml 的 [national_team].dsn；"
            "数据库属于 旧版透视系统项目",
            retryable=True)

    def health(self) -> models.DimensionResult:
        if not self.enabled:
            return self._disabled()
        dep = self._missing_dep()
        if dep:
            return dep
        try:
            with timed() as t, self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT MAX(update_time), COUNT(*) FROM nt_positions_analysis WHERE is_latest = true")
                max_time, count = cur.fetchone()
            return self.ok({"latest_update": str(max_time), "latest_positions": count},
                           as_of=max_time, endpoint="nt_positions_analysis",
                           latency_ms=t["ms"])
        except Exception as e:
            return self._pg_fail(e)

    def position(self, ts_code: str) -> models.DimensionResult:
        """单股的国家队持仓结论（含中文加减仓文案）。"""
        if not self.enabled:
            return self._disabled()
        dep = self._missing_dep()
        if dep:
            return dep
        try:
            with timed() as t, self._connect() as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                # 库内 ts_code 统一为裸 6 位代码（实测 0 行带后缀），查询前去掉市场后缀
                bare = ts_code.split(".")[0]
                cur.execute(_POSITIONS_SQL, (bare,))
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["holder_class"] = _classify_holder(r.get("holder_name") or "")
                for k, v in r.items():
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()
                    elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)) and v is not None:
                        r[k] = float(v)  # Decimal → float
            if not rows:
                cur_time = self._max_update_time()
                return self.not_found(
                    f"国家队最新持仓中未覆盖 {ts_code}",
                    "不代表国家队没买——十大股东披露有门槛；也可能该股不在监测池",
                    as_of=cur_time, endpoint="nt_positions_analysis")
            as_of = max(r.get("update_time") for r in rows if r.get("update_time"))
            return self.ok({"positions": rows, "count": len(rows)},
                           as_of=as_of, endpoint="nt_positions_analysis",
                           latency_ms=t["ms"])
        except Exception as e:
            return self._pg_fail(e)

    def recent_changes(self, limit: int = 20) -> models.DimensionResult:
        """最近一个披露期的加减仓分布（overview 用）。"""
        if not self.enabled:
            return self._disabled()
        dep = self._missing_dep()
        if dep:
            return dep
        try:
            with timed() as t, self._connect() as conn:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT MAX(period_end), MAX(update_time) FROM nt_positions_analysis")
                period_end, update_time = cur.fetchone()
                cur.execute(_CHANGES_SQL, (limit,))
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["holder_class"] = _classify_holder(r.get("holder_name") or "")
                for k, v in r.items():
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()
            return self.ok({"period_end": period_end.isoformat() if period_end else None,
                            "changes": rows},
                           as_of=update_time, endpoint="nt_positions_analysis",
                           latency_ms=t["ms"])
        except Exception as e:
            return self._pg_fail(e)

    def _max_update_time(self) -> str | None:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT MAX(update_time) FROM nt_positions_analysis")
                v = cur.fetchone()[0]
                return v.isoformat() if v else None
        except Exception:
            return None

    def etf_shares(self, weeks: int = 12) -> models.DimensionResult:
        """宽基 ETF 份额序列（汇金仓位代理变量）。

        周频文件（etf_json_path）优先；缺省时回退日频文件（etf_json_daily_path）
        ——云上不迁周频档（2026-11-04 实测），两文件 schema 相同。
        周环比：周频=最新 vs 上一条；日频=最新 vs 7 个自然日前（或之前最近）一条。
        """
        source_kind = "weekly"
        path = None
        if self.etf_json_path:
            p0 = Path(self.etf_json_path)
            if p0.is_file():
                path = p0
        if path is None:
            daily = self.etf_json_daily_path
            if daily:
                p1 = Path(daily)
                if not p1.is_absolute():
                    p1 = PROJECT_ROOT / p1
                if p1.is_file():
                    path = p1
                    source_kind = "daily"
        if path is None:
            return self.fail(
                errors.FILE_MISSING, "未配置 ETF 份额文件",
                "配置 [national_team].etf_json_path（周频）或 etf_json_daily_path"
                "（日频，相对路径按项目根）后重试",
                endpoint="(unset)")
        try:
            with timed() as t:
                # 该文件编码随生成脚本版本变化（老版本 GBK、新版本 UTF-8），两种都兼容
                raw = path.read_bytes()
                for enc in ("utf-8", "gbk"):
                    try:
                        series = json.loads(raw.decode(enc))
                        break
                    except (ValueError, UnicodeDecodeError):
                        continue
                else:
                    raise ValueError("UTF-8/GBK 均无法解析")
        except (ValueError, UnicodeDecodeError) as e:
            return self.fail(errors.PARSE_ERROR, f"ETF 份额 JSON 解析失败：{e}",
                             endpoint=str(path))
        if not series:
            return self.fail(errors.EMPTY_TABLE, "ETF 份额 JSON 为空", endpoint=str(path))
        latest = series[-1]
        wow = None
        if latest.get("全宽基合计"):
            if source_kind == "weekly":
                prev = series[-2] if len(series) >= 2 else None
                if prev and prev.get("全宽基合计"):
                    wow = round(latest["全宽基合计"] - prev["全宽基合计"], 2)
            else:
                wow = _wow_vs_days_ago(series, latest, days=7)
        series_slice = (series[-weeks * 7:] if source_kind == "daily"
                        else series[-weeks:])
        payload = {
            "latest": latest,
            "week_over_week_total": wow,
            "series": series_slice,
            "total_points": len(series),
        }
        return self.ok(payload, as_of=latest.get("date"), endpoint=str(path),
                       latency_ms=t["ms"], kind=source_kind,
                       source="national-team-json")
