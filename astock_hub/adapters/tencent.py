"""腾讯财经行情适配器（qt.gtimg.cn，免费、无鉴权）——第三方交叉验证源。

用于给面板的行情（同花顺 iFinD 优先）做独立对照：现价/涨跌幅/开高低/成交/换手/市值
等字段与同花顺比对，出现差异时在界面上红黄标注，辅助判断哪路数据更接近行情软件。

坑：
- 响应是 GBK 编码，必须 resp.content.decode('gbk', errors='replace')
- 反序列化是 ~ 分隔文本行 "v_sh600519=\"1~名称~代码~现价~...\""
- 字段位置（实测）：[1]名称 [2]代码 [3]现价 [4]昨收 [5]今开 [6]成交量
  （**口径不统一：科创板688/689为股、其余板块为手**，解析时统一转手）
  [30]时间 [31]涨跌额 [32]涨跌幅% [33]最高 [34]最低 [37]成交额(万) [38]换手率%
  [39]市盈率TTM [44]流通市值(亿) [45]总市值(亿) [46]市净率 [47]涨停 [48]跌停
"""

from __future__ import annotations

import httpx

from .. import errors, models
from .base import Adapter, timed

_QT_BASE = "https://qt.gtimg.cn/q="


def _market_prefix(thscode: str) -> str:
    code = thscode.split(".")[0]
    if thscode.endswith((".SH", ".sh")) or code.startswith(("6", "9", "68")):
        return "sh" + code
    if thscode.endswith((".BJ", ".bj")) or code.startswith("920"):
        return "bj" + code
    return "sz" + code


def _f(v: str) -> float | None:
    v = (v or "").strip()
    if v in ("", "-", "—"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


class TencentAdapter(Adapter):
    name = "tencent"
    source = "tencent-http"
    freshness_kind = "realtime"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.base = cfg.get("base_url", _QT_BASE)

    def snapshot(self, thscodes: list[str]) -> models.DimensionResult:
        """腾讯个股行情快照（一次批量，~分隔文本）。"""
        try:
            with timed() as t:
                with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
                    resp = client.get(
                        f"{self.base}" + ",".join(_market_prefix(s) for s in thscodes),
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko)"})
                    resp.raise_for_status()
                    text = resp.content.decode("gbk", errors="replace")
            items: list[dict] = []
            as_of = None
            for line in text.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, _, payload = line.partition("=")
                body = payload.strip().strip(";").strip().strip('"')
                f = body.split("~")
                if len(f) < 48:
                    continue
                ticker = f[2].strip()
                time_raw = f[30].strip()
                if not as_of and len(time_raw) >= 8 and time_raw.isdigit():
                    as_of = f"{time_raw[:4]}-{time_raw[4:6]}-{time_raw[6:8]}"
                # 腾讯 f6 成交量口径不统一：科创板（688/689）为**股**，其余板块为**手**；
                # 统一换算成手（与项目/hithink 口径一致，盘中量比外推才正确）。
                raw_vol = _f(f[6])
                is_star = ticker.startswith(("688", "689"))
                vol = (raw_vol / 100) if (raw_vol is not None and is_star) else raw_vol
                items.append({
                    "symbol": _thscode_from_prefix(key.strip()),
                    "name": f[1].strip(),
                    "price": _f(f[3]),
                    "prev_close": _f(f[4]),
                    "open": _f(f[5]),
                    "volume": vol,
                    "high": _f(f[33]),
                    "low": _f(f[34]),
                    "change": _f(f[31]),
                    "change_pct": _f(f[32]),
                    "time": time_raw,
                    "amount": _f(f[37]) * 1e4 if f[37].strip() not in ("", "-") else None,  # 万→元
                    "turnover_ratio": _f(f[38]),
                    "pe_ttm": _f(f[39]),
                    "float_market_cap": _f(f[44]) * 1e8 if f[44].strip() not in ("", "-") else None,
                    "market_cap": _f(f[45]) * 1e8 if f[45].strip() not in ("", "-") else None,
                    "pb": _f(f[46]),
                    "limit_up_price": _f(f[47]),
                    "limit_down_price": _f(f[48]),
                })
            return self.ok({"item": items, "total": len(items)}, as_of=as_of,
                           endpoint=self.base, latency_ms=t["ms"],
                           note=f"腾讯行情 {len(items)} 条（第三方交叉验证源）")
        except httpx.ConnectError:
            return self.fail(errors.CONNECTION_REFUSED, "连接腾讯行情被拒绝",
                             "网络问题，腾讯源暂时不可用", retryable=True, endpoint=self.base)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            return self.fail(errors.TIMEOUT, f"腾讯行情请求失败：{e}",
                             "稍后重试", retryable=True, endpoint=self.base)

    def snapshot_chunked(self, thscodes: list[str], *, chunk_size: int = 60,
                         max_workers: int = 8, retries: int = 1
                         ) -> models.DimensionResult:
        """全市场分片批量：并发拉取 + 单片失败重试。

        腾讯不支持北交所（.BJ，实测 q=bj920xxx 无数据），自动分离到 unsupported_bj，
        不计入失败；沪深分片失败（重试后）计入 failed_symbols。
        """
        import time
        from concurrent.futures import ThreadPoolExecutor

        bj = [s for s in thscodes if s.upper().endswith(".BJ")]
        cn = [s for s in thscodes if not s.upper().endswith(".BJ")]
        chunks = [cn[i:i + chunk_size] for i in range(0, len(cn), chunk_size)]

        def fetch(chunk: list[str]) -> tuple[list[dict], list[str]]:
            for attempt in range(retries + 1):
                r = self.snapshot(chunk)
                if r.status == "ok":
                    return r.data.get("item", []), []
                if attempt < retries:
                    time.sleep(0.4 * (attempt + 1))
            return [], chunk

        with timed() as t:
            items, failed_symbols = [], []
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for got, bad in pool.map(fetch, chunks):
                    items += got
                    failed_symbols += bad

        as_of = next((it.get("time") for it in items
                      if it.get("time") and len(it["time"]) >= 8), None)
        if as_of and as_of.isdigit():
            as_of = f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:8]}"
        return self.ok({
            "item": items, "total": len(items),
            "universe": len(thscodes), "cn_total": len(cn), "bj_total": len(bj),
            "unsupported_bj": bj, "failed_symbols": failed_symbols,
        }, as_of=as_of, endpoint=self.base, latency_ms=t["ms"],
           note=f"腾讯分片：沪深 {len(items)}/{len(cn)}，北交所 {len(bj)} 只不支持")

    def health(self) -> models.DimensionResult:
        """腾讯行情连通性检查"""
        try:
            with timed() as t:
                self.snapshot(["600519.SH"])  # 贵州茅台
            return self.ok({"status": "ok"}, endpoint=self.base, latency_ms=t["ms"])
        except Exception as e:
            return self.fail(errors.UPSTREAM_ERROR, f"腾讯行情不可用：{e}",
                             "检查网络", retryable=True, endpoint=self.base)


def _thscode_from_prefix(key: str) -> str:
    key = key.strip()
    if key.startswith("v_"):
        key = key[2:]
    if len(key) >= 6:
        code = key[-6:]
        if key.startswith("sh") or code.startswith(("6", "9", "68")):
            return f"{code}.SH"
        if key.startswith("bj") or code.startswith("920"):
            return f"{code}.BJ"
        return f"{code}.SZ"
    return key