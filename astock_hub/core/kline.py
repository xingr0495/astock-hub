"""个股日K + 技术指标 + 关键价位（**本地计算**，不依赖 tickflow 服务）。

为什么单独成模块：这三件事现在是 webapi / MCP / CLI 三个入口共用的能力，放在
webapi 里会让 MCP 和 CLI 反向依赖 FastAPI。取数走 hithink 前复权日线，计算走
`astock_hub.indicators`（从 tickflow 移植、数值逐位一致）。

口径提醒：前端 K 线的 `change_pct` 用**小数**（0.05 = 5%），与 tickflow 一致。
"""

from __future__ import annotations

from .. import models
from ..adapters.hithink import HithinkAdapter

# 指标预热行数：ma60 / momentum_60d / high_60d 需要 60 行，再留一段冗余，
# 否则展示窗口开头那几十行的指标会是空值。
KLINE_WARMUP_DAYS = 120

# 面板 K 线卡片与副图使用的指标列
KLINE_COLS = [
    "date", "open", "high", "low", "close", "volume", "amount", "change_pct",
    "ma5", "ma10", "ma20", "ma30", "ma60",
    "macd_dif", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_14", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower",
    "atr_14", "vol_ratio_5d", "high_60d", "low_60d",
]


def _frame(cfg: dict, sym, days: int, *, name: str | None = None,
           with_raw: bool = False):
    """取该股前复权日线并算好全套指标（统一入口，失败会抛异常）。

    返回 (df, source_label)。取数主源按 `config.toml [data_source] primary`：
    - "axdata"（默认，2026-10-13）：axdata 通达信协议栈，失败降级腾讯→hithink；
    - "astock_data"：a-stock-data 腾讯K线（在线，含当日盘中 bar），失败降级 axdata→hithink；
    - "hithink"：hithink 本地库（同花顺前复权）。
    指标计算各路共用 `astock_hub.indicators`，口径一致。
    """
    import polars as pl

    from ..indicators.sources import (
        indicators_for_symbol,
        indicators_for_symbol_astockdata,
    )

    ds = (cfg.get("data_source") or {}).get("primary", "astock_data")
    df = None
    label = None
    errors: list[str] = []

    # axdata 通达信协议栈（2026-10-13 用户决策升为主源：本地协议无腾讯式突发风控；
    # primary="axdata" 时第一优先，失败再走腾讯→hithink 兜底；旧值行为不变）
    tried_axdata = False
    if ds == "axdata":
        try:
            from ..indicators.sources import indicators_for_symbol_axdata
            df = indicators_for_symbol_axdata(
                sym.ts_code, days=days + KLINE_WARMUP_DAYS,
                name=name, with_raw=with_raw)
            label = "axdata/通达信"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"axdata：{exc}")
        tried_axdata = True

    # 腾讯K线：主源为 astock_data，或 axdata 主源失败后的下一级（即上方注释的
    # “失败再走腾讯”；单写 ds=="astock_data" 接不住 axdata 失败的降级）
    if df is None and (ds == "astock_data" or tried_axdata):
        try:
            df = indicators_for_symbol_astockdata(
                sym.ts_code, days=days + KLINE_WARMUP_DAYS,
                name=name, with_raw=with_raw)
            label = "a-stock-data/腾讯K线"
        except Exception as exc:  # noqa: BLE001 — 主源失败自动回退
            errors.append(f"a-stock-data：{exc}")

    # 二级降级：腾讯K线失败时试 axdata（主源已试过则不重复）
    if df is None and not tried_axdata:
        try:
            from ..indicators.sources import indicators_for_symbol_axdata
            df = indicators_for_symbol_axdata(
                sym.ts_code, days=days + KLINE_WARMUP_DAYS,
                name=name, with_raw=with_raw)
            label = "axdata/通达信"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"axdata：{exc}")

    if df is None:
        ht = HithinkAdapter(cfg.get("hithink", {}))
        df = indicators_for_symbol(ht, sym.ts_code, days=days + KLINE_WARMUP_DAYS,
                                   name=name, with_raw=with_raw)
        label = "hithink 本地库"
        if errors:
            label += f"（主源回退：{'；'.join(errors)}）"

    if "boll_upper" in df.columns and "boll_lower" in df.columns:
        # BOLL 中轨就是 MA20，但显式给一列，前端画图省得自己推
        df = df.with_columns(
            ((pl.col("boll_upper") + pl.col("boll_lower")) / 2).alias("boll_mid"))
    return df, label


def local_kline_dim(cfg: dict, sym, days: int = 120) -> models.DimensionResult:
    """日K + 技术指标（本地计算）。`sym` 是 symbols.SymbolInfo。"""
    import polars as pl

    df, label = _frame(cfg, sym, days)
    df = df.tail(days)
    keep = [c for c in KLINE_COLS if c in df.columns]
    df = df.select([
        pl.col("date").cast(pl.Utf8) if c == "date" else pl.col(c).round(6)
        for c in keep
    ])
    rows = df.to_dicts()
    return models.DimensionResult(
        status=models.OK, source="astockdata/hithink+local-indicators",
        meta=models.SourceMeta(as_of=rows[-1]["date"] if rows else None,
                               freshness_kind="daily"),
        data={"rows": rows, "total_rows": len(rows),
              "source": f"{label} 前复权 + 本地指标（{len(keep)} 列）"},
    )


def levels_dim(cfg: dict, sym, days: int = 250,
               name: str | None = None) -> models.DimensionResult:
    """关键价位（11 类）：成交密集区/POC、枢轴点、前高前低、布林带、Keltner 三档、
    ATR 止损、缺口、斐波那契、整数关口。"""
    from ..indicators.levels import LEVEL_TYPES, compute_levels, summarize_levels

    df, label = _frame(cfg, sym, days, name=name)
    levels = compute_levels(df)
    close = float(df["close"][-1])
    as_of = df["date"][-1]
    return models.DimensionResult(
        status=models.OK, source="astockdata/hithink+local-levels",
        meta=models.SourceMeta(as_of=as_of.isoformat() if as_of else None,
                               freshness_kind="daily",
                               note=f"数据源：{label}"),
        data={"close": close, "summary": summarize_levels(levels, close),
              "level_types": LEVEL_TYPES, "levels": levels,
              "total": sum(len(v) for v in levels.values())},
    )
