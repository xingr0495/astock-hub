# GitHub 发布设置建议

本文件用于仓库公开前的页面配置，不影响程序运行。

## Repository About

**Description**

> 可自托管的 A 股数据聚合与 AI 研究面板。系统名称 Mind AShare；实时看板、个股分析、板块轮动、产业资讯与可选 AI 问答。MIT 开源。

**Website**

部署演示尚未提供时留空，不要填写本机或内网地址。

**Topics**

```text
a-share
stock-market
finance-dashboard
quantitative-finance
self-hosted
fastapi
react
python
ai-research
data-visualization
```

## Social preview

在 GitHub：**Settings → General → Social preview → Upload an image**，上传：

```text
docs/images/social-preview.png
```

图片为 1280×640，系统名称只写作 Mind AShare，仓库名仍为 astock-hub。

## 公开前检查

- [ ] 在干净目录按 `docs/INSTALL.md` 完成后端安装、前端构建和启动
- [ ] 执行 `.venv/Scripts/python.exe -m pytest tests -q`（Linux/macOS 使用 `.venv/bin/python`）
- [ ] 执行 `npm --prefix frontend run build`
- [ ] 验证个股、看板、板块、策略选股和资讯页面
- [ ] 验证无密钥时 AI 功能有明确提示，不影响基础行情功能
- [ ] 扫描 API Key、Webhook、私有纪要、账户数据和本机绝对路径
- [ ] 确认 `config.toml`、`.env`、`data/` 未进入提交
- [ ] README 截图不含账号、密钥、持仓或其他私有内容
- [ ] GitHub About、Topics 和 Social preview 已配置

## 首个 Release

建议使用 `v0.1.0`，标题：

> astock-hub v0.1.0 — Public self-hosted edition

发布说明可包含：

- 自托管实时看板与个股研究
- 板块轮动、策略筛选和产业资讯
- 可选 AI 问答与自有模型配置
- Windows 安装与启动验证
- 中英文 README、安装指南和界面速览

不要在公开发布说明中放收益预测、回测收益、实盘记录或付费引流内容。
