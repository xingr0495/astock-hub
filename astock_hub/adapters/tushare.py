"""Tushare Pro 行情/基本面适配器（api.tushare.pro，token 鉴权）。

定位3 中的"专业付费/自有 key"数据源：用户在「数据源」页填入 Tushare token 后启用。
Tushare Pro 是 HTTP POST 接口（http://api.tushare.pro），body:
  {"api_name": "...", "token": "...", "params": {...}, "fields": "..."}
这里直接用 httpx 调用，运行时不强制依赖 tushare 库（与项目其它适配器风格一致）。

返回的 item 字段对齐项目统一口径（symbol/name/price/change_pct/open/high/low/...）。
"""

from __future__ import annotations

import httpx

from .. import errors, models
from .base import Adapter, timed

_API_BASE = "http://api.tushare.pro"

# Tushare ts_code 带交易所后缀（000001.SZ / 600519.SH / 830799.BJ），项目内部也用
# 同款写法，所以这里直接透传，无需转换。


def _f(v) -> float | None:
    if v is None or v == "" or v != v:  # None / 空串 / NaN
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TushareAdapter(Adapter):
    name = "tushare"
    source = "tushare-pro"
    freshness_kind = "realtime"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.token = (cfg.get("api_key") or cfg.get("token") or "").strip()
        self.base = (cfg.get("base_url") or _API_BASE).strip()

    # ---- 底层 Pro 调用 ----
    def _pro(self, api_name: str, params: dict, fields: str = "") -> list[dict]:
        body = {
            "api_name": api_name,
            "token": self.token,
            "params": params,
            "fields": fields,
        }
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = client.post(self.base, json=body)
            resp.raise_for_status()
            payload = resp.json()
        if payload.get("code") != 0:
            msg = payload.get("msg") or "Tushare 接口返回错误"
            raise RuntimeError(msg)
        data = payload.get("data") or {}
        cols = data.get("fields") or []
        rows = data.get("items") or []
        return [dict(zip(cols, r)) for r in rows]

    def snapshot(self, thscodes: list[str]) -> models.DimensionResult:
        """Tushare 个股最新日线快照（daily 最新交易日）。

        Tushare daily/daily_basic 按 ts_code 取，需要逐 code 拉取后合并；
        批量场景一次请求多个 code 走 daily 列表（trade_date 留空=最新交易日）。
        """
        if not self.token:
            return self.fail(
                errors.NOT_CONFIGURED, "未配置 Tushare token",
                "在「数据源」页填入 Tushare Pro token（tushare.pro 注册获取）",
                endpoint=self.base)
        try:
            with timed() as t:
                ts_codes = ",".join(thscodes)
                daily = self._pro(
                    "daily",
                    {"ts_code": ts_c},
                    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
                )
                # 每日多 code：只保留每只票的最新一条
                latest: dict[str, dict] = {}
                for r in daily:
                    code = r.get("ts_code")
                    if code and code not in latest:
                        latest[code] = r
                items = [
                    {
                        "symbol": r.get("ts_code"),
                        "name": None,  # daily 不含名称，可由 daily_basic/其他源补
                        "price": _f(r.get("close")),
                        "prev_close": _f(r.get("pre_close")),
                        "open": _f(r.get("open")),
                        "high": _f(r.get("high")),
                        "low": _f(r.get("low")),
                        "change": _f(r.get("change")),
                        "change_pct": _f(r.get("pct_chg")),
                        "time": r.get("trade_date"),
                        # Tushare 单位：vol=手, amount=千元
                        "volume": _f(r.get("vol")),
                        "amount": _f(r.get("amount")) * 1e3 if r.get("amount") not in (None, "") else None,
                    }
                    for r in latest.values()
                ]
                # 补名称（namechange 取每票最新名）
                try:
                    names = self._pro(
                        "namechange",
                        {"ts_code": ts_codes},
                        "ts_code,name,start_date,end_date",
                    )
                    name_map = {}
                    for r in names:
                        c = r.get("ts_code")
                        if c and c not in name_map:
                            name_map[c] = r.get("name")
                    for it in items:
                        it["name"] = name_map.get(it["symbol"])
                except Exception:  # noqa: BLE001 — 名称失败不影响行情
                    pass
            return self.ok({"item": items, "total": len(items)},
                           endpoint=self.base, latency_ms=t["ms"],
                           note=f"Tushare 行情 {len(items)} 条")
        except httpx.ConnectError:
            return self.fail(errors.CONNECTION_REFUSED, "连接 Tushare 被拒绝",
                             "检查网络/ base_url", retryable=True, endpoint=self.base)
        except (httpx.TimeoutException, httpx.HTTPStatusError, RuntimeError) as e:
            return self.fail(errors.UPSTREAM_ERROR, f"Tushare 请求失败：{e}",
                             "稍后重试或检查 token/积分权限", retryable=True,
                             endpoint=self.base)

    def health(self) -> models.DimensionResult:
        """Tushare 连通性检查"""
        if not self.token:
            return self.ok({"status": "skipped", "note": "未配置 api_key"},
                           endpoint=self.base)
        try:
            with timed() as t:
                self._pro("trade_cal",
                          {"ts_code": "000001.SH", "start_date": "20260101", "end_date": "20260110"},
                          "exchange_cal_name")
            return self.ok({"status": "ok"}, endpoint=self.base, latency_ms=t["ms"])
        except Exception as e:
            return self.fail(errors.UPSTREAM_ERROR, f"Tushare 不可用：{e}",
                             "检查 api_key 和网络", retryable=True, endpoint=self.base)


def probe(api_key: str, api_base_url: str | None = None,
          timeout_s: float = 25.0) -> tuple[bool, str]:
    """数据源页「测试连接」：最小实拉 trade_cal（交易日历，基础免费接口）。

    复用 TushareAdapter._pro（统一 POST 协议与 code!=0 报错口径）。
    返回 (ok, 中文结论)：token 错误/无积分、网络失败分别如实说明。
    """
    key = (api_key or "").strip()
    if not key:
        return False, "Token 为空（在 tushare.pro 个人中心复制）"
    cfg: dict = {"api_key": key}
    if api_base_url:
        cfg["base_url"] = api_base_url
    adapter = TushareAdapter(cfg)
    try:
        rows = adapter._pro(
            "trade_cal",
            {"exchange": "SSE", "start_date": "20260101", "end_date": "20260110"},
            "exchange,cal_date,is_open",
        )
    except httpx.TimeoutException:
        return False, f"连接超时（{timeout_s:.0f}s）——检查网络或接口地址后重试"
    except httpx.HTTPError as e:
        return False, f"网络请求失败：{e}"
    except RuntimeError as e:
        return False, f"Token 校验未通过：{e}（到 tushare.pro 核对 token 与积分权限）"
    except Exception as e:  # noqa: BLE001 — 探测绝不抛给页面
        return False, f"探测失败：{e}"
    return True, f"连接正常：交易日历返回 {len(rows)} 行，token 有效"
