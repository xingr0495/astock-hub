# 免费版云服务器安装教程（新手详细版）

本教程适用于 **Mind AShare 免费版（astock-hub）**，面向没有编程或 Linux 部署经验的使用者。你不需要自己编写代码，大部分步骤只需复制命令、粘贴到服务器终端，再核对结果。

## 完成本教程后，你会得到什么

- 一个可以通过浏览器打开的 A 股数据面板；
- 个股行情、指数、K 线、分时、板块和产业资讯等页面；
- 服务器重启后面板自动恢复；
- 只有你自己当前使用的网络可以访问面板；
- 一套可以反复使用的安装、检查和升级方法。

**预计用时：** 首次约 30–60 分钟，不包括大型历史数据的下载或传输时间。云服务器、付费数据源和大模型可能分别产生费用，请按自己的需要选择。

教程章节按“准备 → 登录 → 安装 → 启动 → 导入数据 → 验收 → 排错”排列，后续视频版可以直接沿用同一顺序。

本教程根据 2026-11-23 的真实部署整理，实测环境为：

- 华为云 Flexus，Ubuntu 24.04 64 位，x86_64；
- 2 核 CPU、2 GiB 内存、40GB 系统盘；
- Python 3.12、Node.js 18；
- 免费版端口 `8756`；
- systemd 常驻，华为云安全组 + UFW 双重白名单；
- 公开测试数据与真实看板、市场总览接口验收。

> 如果只是体验个股行情，可以使用配置较低的机器。若要长期运行、频繁做全市场计算或给多人使用，建议至少 **2 核 4GB**。2GB 内存的机器建议配置 **4GB 交换空间**。

---

## 一、先看懂安装后的结构

```text
你的电脑或手机
      │  浏览器访问 http://服务器公网IP:8756
      ▼
华为云安全组 + 服务器 UFW（只允许你的公网 IP）
      ▼
免费版 FastAPI 服务（systemd 常驻）
      ├─ 腾讯 / 通达信公开行情
      ├─ 本地历史行情 Parquet
      ├─ 可选 hithink-finance
      └─ 可选大模型 API
```

免费版采用**机主单用户模式，没有登录密码**。因此，不能只靠“别人不知道网址”来保护它；公网部署时必须限制来源 IP，正式对外开放还应另外配置 HTTPS、身份认证和速率限制。

---

## 二、准备服务器与登录方式

### 1. 创建或确认服务器

推荐选择：

- 操作系统：Ubuntu 24.04 64 位；
- CPU：至少 2 核；
- 内存：至少 2GB，推荐 4GB；
- 磁盘：至少 40GB；
- 公网 IP：必须分配。

Ubuntu 实例的初始用户通常是 `root`。如果不知道服务器密码，到云控制台的实例详情页找到 **重置密码**，重置后按提示重启。

> 云控制台账号密码、服务器登录密码、许可证密钥和大模型 Key 是四种不同的东西，不要混用。

### 2. 放行 SSH

在云控制台的**安全组或防火墙**中添加入站规则：

| 协议 | 端口 | 来源 | 用途 |
|---|---:|---|---|
| TCP | 22 | 你的当前公网 IP `/32` | 自己管理服务器 |
| TCP | 8756 | 你的当前公网 IP `/32` | 自己访问免费版面版 |

`8756` 可以在程序安装完成后再添加。不要把这两个端口直接对 `0.0.0.0/0` 开放。

查询自己当前公网 IP 的一种办法：

```bash
curl -4 https://ifconfig.me/ip
```

### 3. 登录服务器

Windows、macOS 或 Linux 都可以使用 SSH：

```bash
ssh root@服务器公网IP
```

第一次登录时确认服务器指纹，然后输入服务器密码。若使用密钥登录，需要先把**公钥**写入服务器用户的 `~/.ssh/authorized_keys`，私钥只留在自己的电脑上。

公钥写入命令示例：

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh && printf '%s\n' '在这里粘贴你的公钥' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
```

不要把私钥内容发给任何人，也不要上传到 GitHub。

---

## 三、安装系统环境

登录服务器后执行以下步骤。

### 1. 设置北京时间

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

检查：

```bash
date
```

### 2. 安装 Python、Node.js 与构建工具

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nodejs npm build-essential curl git
```

检查版本：

```bash
python3 --version && node --version && npm --version
```

要求：

- Python ≥3.11；
- Node.js ≥18；
- npm 可用。

Ubuntu 24.04 自带的 Python 3.12 和 Node.js 18 已实测可用。较旧系统如果 Node.js 版本不足，请先从可信来源安装新版，不要继续使用旧版强行构建。

### 3. 小内存服务器增加交换空间

**已经是 4GB 或更大内存、或已有足够 swap 时可以跳过。**

以下命令只在没有启用 swap 时创建 4GB：

```bash
if ! swapon --show=NAME --noheadings | grep -q .; then sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && printf '/swapfile none swap sw 0 0\n' | sudo tee -a /etc/fstab; fi
```

检查：

```bash
free -h && swapon --show
```

交换空间不是性能升级，只是降低内存不足时进程被系统杀死的概率。全市场计算仍然需要更慢或更谨慎地执行。

---

## 四、下载并安装免费版

### 方法一：从 GitHub 克隆

```bash
git clone https://github.com/xingr0495/astock-hub.git
cd astock-hub
```

如果服务器访问 GitHub 很慢或失败，可以在自己的电脑下载源码，再用 SCP 上传；不要把本机 `.git`、`.venv`、`node_modules`、`data/`、`config.toml` 和 `.env` 一并打包上去。

### 安装后端与前端

在项目根目录运行：

```bash
bash install.sh
```

该脚本会：

1. 创建 `.venv`；
2. 安装 Python 后端依赖；
3. 安装前端依赖；
4. 构建生产页面；
5. 不自动启动公网服务。

若要同时安装测试依赖：

```bash
.venv/bin/python -m pip install -e ".[web,dev]"
```

### 创建自己的配置

```bash
cp config.example.toml config.toml
```

初次安装不要从其他机器复制 `config.toml`。在面板的“数据源”“大模型”等页面按需填写自己的配置即可。

---

## 五、先在本机地址验证，再开放公网

在项目根目录临时启动：

```bash
.venv/bin/python -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756
```

另开一个 SSH 窗口检查：

```bash
curl http://127.0.0.1:8756/api/health
```

能返回 JSON 就说明后端已经启动。确认后，在临时启动窗口按 `Ctrl+C` 停止，再进入下一步配置 systemd。

如果暂时不配置 systemd，也可以保持这个窗口运行，但关闭 SSH 后进程可能退出，不适合长期使用。

---

## 六、配置开机启动与异常自动恢复

以下做法使用当前登录用户和当前目录，不需要手工把路径写成 `/root/...`：

```bash
APP_DIR="$HOME/astock-hub"
APP_USER="$USER"
sudo tee /etc/systemd/system/astock-hub.service > /dev/null <<EOF
[Unit]
Description=Mind AShare Free Edition
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=TZ=Asia/Shanghai
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/python -m uvicorn astock_hub.webapi:app --host 0.0.0.0 --port 8756
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

启动并设为开机自启：

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now astock-hub
```

检查：

```bash
systemctl is-active astock-hub && systemctl is-enabled astock-hub && curl http://127.0.0.1:8756/api/health
```

预期看到 `active`、`enabled` 和健康检查 JSON。

### 防火墙再限制一次

把 `<你的公网IP>` 换成实际地址，保留 `/32`：

```bash
sudo ufw default deny incoming && sudo ufw default allow outgoing && sudo ufw allow from <你的公网IP>/32 to any port 22 proto tcp && sudo ufw allow from <你的公网IP>/32 to any port 8756 proto tcp && sudo ufw --force enable
```

检查：

```bash
sudo ufw status
```

完成云安全组和 UFW 两处设置后，在自己电脑浏览器打开：

```text
http://服务器公网IP:8756
```

---

## 七、为什么刚安装后有些栏目没有数据

免费版并不是所有栏目都只依赖实时公开接口。

| 栏目 | 初次安装即可使用 | 还依赖什么 |
|---|---|---|
| 个股日 K、分时、基础行情 | 通常可以 | 腾讯 / 通达信公开接口 |
| 市场总览六个指数 | 可以 | hithink 不可用时回退腾讯免费源 |
| 实时看板、市场宽度、涨跌停统计 | 需要历史库 | 全市场日线与名称表 |
| 主线、板块轮动、成分分析 | 需要历史库 | 行业/概念成分、涨停池及历史行情 |
| 策略扫描 | 需要历史库 | 本地全市场数据 |
| 产业资讯 | 需要先生成资讯档 | `data/investment_news/news.json` |
| PE/PB/PS 估值 | 不一定 | hithink 或其他已接入的授权数据源 |
| AI 对话、AI 摘要、生成式复盘 | 不一定 | 用户自己的大模型配置 |

所以，“个股能看、看板为空”通常不是程序崩溃，而是尚未初始化全市场历史数据。

---

## 八、初始化或导入公开数据

### 方式 A：自行授权并初始化 hithink-finance

如果需要使用 hithink-finance，请按其官方说明安装、授权后执行：

```bash
hithink-finance data init
hithink-finance data sync
```

它是可选增强源，不是所有功能的强制条件。指数有腾讯回退，历史行情可以使用 axdata / 通达信数据，股票名称可复用本地缓存。

### 方式 B：放入自己已有的 Parquet 数据

常见目录与用途：

| 文件或目录 | 用途 |
|---|---|
| `data/market/kline_daily_raw/_all.parquet` | 全市场原始日线 |
| `data/market/kline_daily/_all.parquet` | 前复权日线 |
| `data/market/adj_factor.parquet` | 复权因子 |
| `data/hithink_cache/name_map.parquet` | 股票代码与名称 |
| `data/hithink_cache/membership_concept.parquet` | 概念成分 |
| `data/hithink_cache/membership_industry.parquet` | 行业成分 |
| `data/hithink_cache/calendar.parquet` | 交易日历 |
| `data/hithink_cache/st_symbols.parquet` | ST 名单 |
| `data/limit_pool/` | 历史涨停池 |
| `data/investment_news/news.json` | 产业资讯展示 |

导入后重启服务：

```bash
sudo systemctl restart astock-hub
```

**不要迁移以下内容到其他用户或公开仓库：** `auth.json`、`auth_service_token`、`credentials.json`、`.env`、`config.toml`、`users/`、`local_docs/`、私有纪要、模拟盘台账和回测数据。

### 产业资讯刷新

没有资讯档时，可按面板提示刷新，或使用：

```bash
.venv/bin/astock-hub refresh --source news --force
```

公开 RSS 抓取不依赖大模型；中文 AI 要点和摘要需要自己的大模型配置。AI 失败时应有新闻列表或明确的降级提示，而不是伪造内容。

---

## 九、完整验收顺序

### 1. 后端测试

```bash
.venv/bin/python -m pytest tests -q
```

### 2. 前端生产构建

```bash
npm --prefix frontend run build
```

### 3. 基础行情

```bash
curl 'http://127.0.0.1:8756/api/kline?code=600519&days=30'
```

### 4. 六指数回退

```bash
curl http://127.0.0.1:8756/api/indexes
```

应看到上证指数、深证成指、创业板指、科创50、沪深300和中证500六项数据。

### 5. 看板与产业资讯

```bash
curl http://127.0.0.1:8756/api/dashboard
curl 'http://127.0.0.1:8756/api/news?limit=5'
```

首次看板计算可能较慢。小内存服务器不要同时发起多个全市场计算请求。

---

## 十、常见问题

### 1. SSH 连接 22 端口超时

这是网络或安全组问题，通常还没到账号密码验证阶段。检查：

- 云控制台安全组是否放行 TCP 22；
- 来源 IP 是否仍是你当前的公网 IP；
- 服务器公网 IP 是否填写正确；
- 实例是否正在运行。

### 2. 提示 `Permission denied (publickey,password)`

网络已经连通，但登录认证失败。常见原因：

- 公钥写到了其他用户目录；
- 使用了 `root` 登录，却把公钥写进 `ubuntu` 的目录；
- `authorized_keys` 内容不完整或权限不正确；
- 私钥没有随 SSH 命令指定。

可在服务器上检查：

```bash
whoami && echo $HOME && ls -l ~/.ssh/authorized_keys
```

### 3. Windows 提示私钥权限过于开放

Windows OpenSSH 会拒绝权限不安全的私钥。把私钥放在当前用户的 `.ssh` 目录，并只给当前用户读取权限。

不要放在不支持 Windows ACL 的 FAT/exFAT 盘上；如果遇到 `No permissions are set`，应先把私钥移到系统盘用户目录，再设置权限。

### 4. 页面能打开，但看板和板块分析为空

先检查本地历史库，不要先怀疑安全组：

```bash
ls -lh data/market/kline_daily_raw/_all.parquet data/hithink_cache/name_map.parquet
```

文件不存在时，按第八节初始化或导入。仅有个股行情接口正常，不代表全市场历史库已经存在。

### 5. 产业资讯没有任何内容

检查：

```bash
ls -lh data/investment_news/news.json
```

缺失时先刷新资讯。只有新闻源抓取成功、资讯档落盘后，页面才有内容可展示。

### 6. 估值显示不可用

估值不是本地历史库自动包含的数据。未配置 hithink 或其他已接入估值源时，页面应显示“暂不可用”，而不是 500。

可选择的替代方案包括 Tushare 或 TdxQuant，但需要自己的凭证，且各源覆盖范围不同，不能认为所有字段都能等价替代。

### 7. 市场总览因指数接口失败而无法显示

请更新到包含腾讯指数回退的免费版。新版在 hithink 未安装或未授权时，会改用腾讯公开指数接口，不再让整个市场总览被单个数据源拖垮。

### 8. 服务突然退出或页面在计算时断连

检查是否内存不足：

```bash
free -h && sudo journalctl -u astock-hub --since '10 minutes ago'
```

如果日志出现 `OOM`、`oom-kill` 或 `Killed process`，说明进程被内存保护机制终止。2GB 内存机器建议配置 4GB swap，并避免同时计算两个全市场看板或同时初始化多个大型数据集。

### 9. 页面像是旧版本

先确认前端重新构建并重启服务：

```bash
npm --prefix frontend run build && sudo systemctl restart astock-hub
```

再在浏览器按 `Ctrl+F5` 强制刷新。不要只复制旧的前端源码或旧的 `dist`，否则可能把新版本覆盖回去。

### 10. 家里宽带换 IP 后无法访问

这是白名单的正常表现。到云控制台把 SSH 和 8756 的来源更新为新 IP，同时更新 UFW 规则。动态 IP 环境下需要定期维护白名单。

### 11. GitHub 克隆很慢或失败

可以在自己的电脑下载源码后通过 SCP 上传。上传前排除 `.git`、虚拟环境、`node_modules`、运行数据、本地配置和密钥，避免把另一台机器的环境带过去。

### 12. 提示缺少 pytest、python-multipart 或其他 Python 包

先更新到最新版本，再按项目声明重新安装：

```bash
.venv/bin/python -m pip install -e ".[web,dev]"
```

不要在系统 Python 中随意全局安装另一套版本；优先使用项目自己的 `.venv`。

### 13. 测试数据日期不是今天

静态公开测试快照不会自动更新。例如周末或盘后导入的数据可能显示最近交易日，而不是日历上的今天。需要每日更新时，应另行配置并验证数据更新链。

---

## 十一、升级与回滚

### 升级

```bash
git pull
bash install.sh
.venv/bin/python -m pip install -e ".[web,dev]"
.venv/bin/python -m pytest tests -q
npm --prefix frontend run build
sudo systemctl restart astock-hub
```

### 回滚

如果新版本验证失败，先停止对外访问，再切回已知正常的代码与前端构建：

```bash
git checkout <已知正常的提交号>
bash install.sh
sudo systemctl restart astock-hub
```

回滚前保留自己的 `config.toml` 和数据目录，不要为了回退代码而删除用户数据。

