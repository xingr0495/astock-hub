# 第三方组件与许可声明（THIRD_PARTY_NOTICES）

astock-hub 使用了以下第三方组件。按"代码是否随本包交付"分两类说明。

> 说明：本文件只管**代码**的许可。**数据**（行情、研报、纪要等）不归任何代码许可证授权——数据的合规使用见 `docs/DATA_SOURCES.md`。

---

## 一、代码被移植/复制进本包的组件（必须保留其许可）

### a-stock-data（simonlin1212/a-stock-data）
- **许可证**：Apache License 2.0
- **关系**：`astock_hub/adapters/astock_data.py` 移植了其中的关键函数（腾讯K线、通达信盘后包等）
- **义务**：保留 Apache-2.0 许可与出处；移植/修改处在源文件头部已注明
- **许可文本**：`licenses/a-stock-data.LICENSE`
- **作者**：Simon 林（github.com/simonlin1212）

### tickflow-stock-panel（shy3130/tick-stock-panel）
- **许可证**：MIT License
- **关系**：技术指标计算（`astock_hub/indicators/` 等）与面板结构借鉴/移植自该项目
- **义务**：保留 MIT 版权与许可声明
- **许可文本**：`licenses/tick-stock-panel.LICENSE`

### TradingAgents-astock（simonlin1212 的 TradingAgents fork）
- **许可证**：Apache License 2.0（含 NOTICE 文件）
- **关系**：个股「深度多 Agent 分析」子进程调用；**若随包交付其代码**，须同时带上 LICENSE 与 NOTICE
- **上游**：TauricResearch/TradingAgents（Apache-2.0）
- **许可文本**：`licenses/TradingAgents-astock.LICENSE`、`licenses/TradingAgents-astock.NOTICE`

---

## 二、仅作外部依赖、不随包交付其代码的组件

以下为运行/构建期依赖（pip / npm 安装时拉取），本包不复制其源码，仅在此登记：

| 组件 | 许可证 |
|------|--------|
| FastAPI / Starlette / Pydantic / Uvicorn | MIT |
| polars | MIT |
| httpx | BSD-3-Clause |
| duckdb | MIT |
| openpyxl / fpdf2 | MIT / MIT |
| React / Vite / Tailwind CSS | MIT |
| ECharts | Apache-2.0 |
| mootdx / akshare（部分源使用） | MIT / MIT |

> superpowers-zh（jnMetaCode/superpowers-zh，MIT）与 a-stock-data 技能仅作本机开发/取数环境使用，不随本包分发。

---

## 三、重要边界（许可证管不到的两件事）

1. **数据**：上述许可证只覆盖代码。行情、研报、纪要等数据的合规使用由使用方自行负责（见 `docs/自建版数据接入说明.md`）。
2. **证券业务**：本软件是技术工具，不构成投资建议。收费荐股、代客理财等需相应金融资质，与本软件的代码许可无关（见 `README.md` 的「许可与免责声明」）。
