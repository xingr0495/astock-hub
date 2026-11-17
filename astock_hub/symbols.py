"""股票代码归一化：6 位裸码 ↔ 带交易所后缀（thscode）。

各数据源要的代码形态不同：
- tickflow / 国家队 PG / hithink：带后缀（600519.SH）
- DSA / zsxq：6 位裸码
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 6 位 A 股代码 → 交易所。先匹配多位前缀（920 北交所优先于 900 沪市B股），再按首位兜底
_PREFIX_EXCHANGE = [
    ("920", "BJ"), ("43", "BJ"), ("83", "BJ"), ("87", "BJ"),
    ("900", "SH"), ("60", "SH"), ("68", "SH"),
    ("00", "SZ"), ("30", "SZ"), ("200", "SZ"),
]
_FIRST_CHAR_EXCHANGE = {
    "6": "SH", "9": "SH",
    "0": "SZ", "2": "SZ", "3": "SZ",
    "4": "BJ", "8": "BJ",
}

_SUFFIX_RE = re.compile(r"^(\d{6})[._]?(SH|SZ|BJ)$", re.IGNORECASE)
_PREFIX_RE = re.compile(r"^(SH|SZ|BJ)[._]?(\d{6})$", re.IGNORECASE)
_BARE_RE = re.compile(r"^\d{6}$")


@dataclass
class SymbolInfo:
    bare: str  # 6 位裸码，如 600519
    exchange: str  # SH / SZ / BJ

    @property
    def ts_code(self) -> str:
        return f"{self.bare}.{self.exchange}"


def is_code_like(text: str) -> bool:
    """输入看起来像个代码（而不是股票名称）。"""
    text = text.strip()
    return bool(_BARE_RE.match(text) or _SUFFIX_RE.match(text) or _PREFIX_RE.match(text))


def normalize(code: str) -> SymbolInfo:
    """把各种写法的股票代码归一成 SymbolInfo。

    接受：600519 / 600519.SH / 600519SH / SH600519 / sh600519。
    无法识别时抛 ValueError（调用方应转成名称搜索或报错）。
    """
    text = code.strip()
    m = _SUFFIX_RE.match(text)
    if m:
        return SymbolInfo(bare=m.group(1), exchange=m.group(2).upper())
    m = _PREFIX_RE.match(text)
    if m:
        return SymbolInfo(bare=m.group(2), exchange=m.group(1).upper())
    if _BARE_RE.match(text):
        exchange = None
        for prefix, ex in _PREFIX_EXCHANGE:
            if text.startswith(prefix):
                exchange = ex
                break
        exchange = exchange or _FIRST_CHAR_EXCHANGE.get(text[0])
        if exchange is None:
            raise ValueError(f"无法判断交易所：{text}")
        return SymbolInfo(bare=text, exchange=exchange)
    raise ValueError(f"不是有效的 6 位股票代码：{code}")


# ── A 股股票口径（单点定义，口径注册表原则） ─────────────────────────
# 白名单前缀：沪 600/601/603/605/688/689，深 000/001/002/003/300/301/302，
# 北 43x/83x/87x/920。**不含** B 股（900/200）、基金/ETF（51/56/58/15/16/18/159）、
# 转债国债（10x-14x 等）——全市场帧里这些品种会污染涨停/涨跌家数统计
# （2026-09-30 实测：regime 64 = 52 真涨停 + 12 只货币ETF/转债/LOF）。
_A_PREFIX3 = ("600", "601", "603", "605", "688", "689",
              "000", "001", "002", "003", "300", "301", "302", "920")
_A_PREFIX2 = ("43", "83", "87")


def is_a_share_stock(symbol: str) -> bool:
    """symbol（600519.SH / 600519）是否 A 股股票（含北交所，不含 B 股/基金/债券）。"""
    bare = symbol.split(".")[0].split("_")[0]
    if len(bare) < 6:
        bare = bare.zfill(6)
    return bare[:3] in _A_PREFIX3 or bare[:2] in _A_PREFIX2


def polars_is_a_share_stock(col):
    """polars 向量版（口径与 is_a_share_stock 完全一致）。"""
    import polars as pl
    bare = col.str.replace(r"[._-].*$", "").str.zfill(6)
    return bare.str.slice(0, 3).is_in(_A_PREFIX3) | bare.str.slice(0, 2).is_in(_A_PREFIX2)
