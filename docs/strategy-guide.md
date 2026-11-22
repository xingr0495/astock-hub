# 策略开发指南

> 面向"策略选股 → 创建策略"弹窗中两种策略写法（AI 生成 / 自定义代码）。
> 本文是本项目自有的开发指南，不依赖外部仓库。

## 一、两种创建方式

| 方式 | 适合人群 | 入口 |
|------|---------|------|
| AI 生成 | 不写代码，用自然语言描述规则 | 弹窗「AI 生成」Tab |
| 自定义代码 | 有 Python 基础，深度定制 | 弹窗「自定义代码」Tab |

## 二、策略文件结构（自定义代码）

策略是一个 Python 文件，核心由 `META` 元信息 + `filter()` 过滤函数 + 信号/止损参数组成。

```python
"""策略简短描述"""
import polars as pl

META = {
    "id": "custom_my_strategy",      # 唯一 id（字母数字下划线）
    "name": "我的策略",               # 显示名
    "description": "策略描述",
    "tags": ["自定义"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {                # 基础过滤（先筛掉不符合的票）
        "price_min": 5, "price_max": 200,
        "market_cap_min": None,
        "float_cap_min": 30e8, "float_cap_max": 1500e8,
        "amount_min": None,
        "turnover_min": 1,
        "exclude_st": True, "exclude_new_days": 30,
        "boards": ["沪主板", "深主板", "创业板", "科创板"],
    },
    "params": [],                     # 策略参数（弹窗「参数」Tab 可编辑）
    "scoring": {                      # 打分权重（用于排序）
        "change_pct": 0.5, "vol_ratio_5d": 0.5,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "polars_expr"     # 执行后端：polars_expr / matrix_native
ENTRY_SIGNALS = ["signal_n_day_high"] # 入场信号
EXIT_SIGNALS = ["signal_ma20_breakdown"]  # 出场信号
STOP_LOSS = -0.05                     # 止损比例（-5%）
MAX_HOLD_DAYS = 20                    # 最长持有交易日

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """返回一个 Polars 布尔表达式：为 True 的行被保留。"""
    return (
        (pl.col("close") > pl.col("ma20"))
        & (pl.col("volume") > pl.col("vol_ma5") * 1.5)
    )
```

## 三、常用字段（filter 可用）

| 字段 | 含义 |
|------|------|
| `close` / `open` / `high` / `low` | 前复权价 |
| `raw_close` | 原始价（不复权，判涨跌停用） |
| `volume` | 成交量（手） |
| `amount` | 成交额（元） |
| `ma5` / `ma10` / `ma20` / `ma60` | 均线 |
| `vol_ma5` | 5 日均量 |
| `change_pct` | 当日涨跌幅（小数） |
| `vol_ratio_5d` | 量比（当日量 / 5 日均量） |
| `turnover_rate` | 换手率 |
| `signal_limit_up` / `signal_limit_down` | 涨/跌停信号（bool） |
| `consecutive_limit_ups` | 连板数 |

## 四、执行后端怎么选

- **polars_expr**（默认）：用 Polars 表达式写 `filter`，简单直观，适合大多数筛选逻辑。
- **matrix_native**：用矩阵原生写法（`MarketDataMatrix` / `SignalMatrix` / `matrix_feature`），适合需要向量化批量计算、性能敏感或复用矩阵特征的复杂策略。

## 五、信号与出场

- `ENTRY_SIGNALS`：入场信号列表（如 `signal_n_day_high`、`signal_ma_golden_cross`），多个信号默认"任一满足即入场"。
- `EXIT_SIGNALS`：出场信号列表（如 `signal_ma20_breakdown`、`signal_trailing_stop`）。
- `STOP_LOSS`：固定止损比例（如 -0.05 = -5%）。
- `MAX_HOLD_DAYS`：最长持有交易日，到期强制出场。

## 六、参数（params）

`META["params"]` 声明策略参数后，可在弹窗「参数」Tab 里直接改值而不用改代码。参数以 dict 传入 `filter(df, params)` 的第二个参数 `params`。

```python
"params": [
    {"key": "ma_window", "label": "均线窗口", "type": "int", "default": 20, "min": 5, "max": 60},
    {"key": "vol_mult", "label": "放量倍数", "type": "float", "default": 1.5, "min": 1.0, "max": 5.0},
]
```

`filter` 里用 `params["ma_window"]`、`params["vol_mult"]` 读取。

## 七、AI 生成怎么写描述

用自然语言把规则讲清楚即可，AI 会转成代码。建议包含：

1. **入场条件**：如"收盘价站上 20 日均线，且当日成交量是 5 日均量的 1.5 倍以上"
2. **出场条件**：如"跌破 20 日均线出场"或"持有 20 天或亏损 5% 止损"
3. **基础过滤**：如"剔除 ST、上市不足 30 天、价格低于 5 元"
4. **打分排序**（可选）：如"按当日涨幅和量比各半打分，取前 100 只"

生成后切到「代码」Tab 检查，再到「参数」Tab 微调，最后保存。

## 八、调试建议

- 先用小窗口（近 20~50 个交易日）跑通，再拉长历史验证。
- 注意 `close`（前复权）与 `raw_close`（原始价）的区别：判涨跌停、除权日用 `raw_close`，趋势/均线用 `close`。
- `filter` 返回的必须是 Polars 表达式（`pl.Expr`），不要返回 Python bool。
- 保存前在「预览」里确认入场/出场信号、止损、参数都符合预期。
