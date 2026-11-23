# 安装与首次使用

`astock-hub` 由 **Python 后端 + React 前端**组成。安装完成后，后端会同时托管前端页面，只需启动一个进程、使用一个端口。

系统显示名称为 **Mind AShare**，技术目录、Python 包和命令行仍使用 `astock-hub`。

## 一键安装（推荐）

先按下方对应系统说明安装一次 **Python 3.11+** 和 **Node.js 18+**，以后安装和更新项目都可以使用一键脚本：

- **Windows**：双击项目根目录的 **`install.cmd`**。脚本完成后会启动面板并打开浏览器。
- **Linux / macOS**：在项目根目录执行 **`bash install.sh --start`**。

如果只想安装、暂不启动：

- Windows：`powershell -ExecutionPolicy Bypass -File install.ps1`
- Linux / macOS：`bash install.sh`

一键脚本会自动完成：检查版本 → 创建 `.venv` → 安装后端 → 安装前端 → 构建页面。重复执行时会复用现有虚拟环境。**它不会自动安装或升级系统 Python/Node.js，也不会主动开放公网访问。**

## 安装方式总览

| 使用环境 | 安装方式 | 当前验证情况 |
|----------|----------|--------------|
| Windows 10/11 | Python 虚拟环境 + npm 构建 | 已实测 |
| Ubuntu / Debian | apt 安装运行环境 + Python 虚拟环境 | 同一安装路径，待对应系统验收 |
| macOS（Intel / Apple Silicon） | Homebrew + Python 虚拟环境 | 同一安装路径，待对应系统验收 |
| Fedora / Arch | 系统包管理器 + Python 虚拟环境 | 同一安装路径，待对应系统验收 |
| 云服务器 | 推荐 Ubuntu，按 Linux 方式安装 | 不建议直接开放公网 |
| Android / iPhone / iPad | 不安装后端，用浏览器访问电脑或服务器 | 需先显式开启局域网或远程访问 |

> 当前仓库**未提供官方 Docker 镜像或 Dockerfile**。请勿把其他项目的 Docker 命令直接套用到本仓库。

---

## 一、Windows 10 / 11

### 1. 安装运行环境

分别安装：

- **Python 3.11 或更高版本**：从 [python.org](https://www.python.org/downloads/) 下载，安装时勾选 **Add python.exe to PATH**。
- **Node.js 18 或更高版本**：从 [nodejs.org](https://nodejs.org/) 下载 LTS 版本，npm 会随 Node.js 一起安装。

安装后关闭并重新打开 PowerShell，检查：

```powershell
python --version
node --version
npm --version
```

Python 显示 3.11 或更高、Node.js 显示 18 或更高即可。

### 2. 打开项目目录

在 GitHub 页面选择 **Code → Download ZIP** 并解压，或使用 Git 克隆仓库。然后在项目根目录打开 PowerShell。

如果当前不在项目目录：

```powershell
cd path\to\astock-hub-free
```

### 3. 安装后端和前端

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e ".[web]"
npm --prefix frontend ci
npm --prefix frontend run build
```

如果电脑安装了 Python Launcher，也可以把第一条命令换成 `py -3.11 -m venv .venv`。

### 4. 启动

```powershell
.venv\Scripts\python.exe -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756
```

浏览器打开 **http://127.0.0.1:8756**。

保持这个 PowerShell 窗口运行；关闭窗口或按 `Ctrl+C` 会停止服务。

也可以使用仓库脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-web.ps1
```

---

## 二、Ubuntu / Debian

推荐 Ubuntu 24.04 或更新版本。先确认系统自带版本：

```bash
python3 --version
node --version
npm --version
```

要求 Python ≥3.11、Node.js ≥18。Ubuntu 22.04 默认 Python 版本可能过旧，需先通过可信来源安装新版本；Node.js 过旧时，请从 [Node.js 官网](https://nodejs.org/)安装当前 LTS。

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm build-essential
```

### 2. 安装项目

在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[web]"
npm --prefix frontend ci
npm --prefix frontend run build
```

### 3. 启动

```bash
.venv/bin/python -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756
```

本机浏览器打开 **http://127.0.0.1:8756**。

---

## 三、macOS（Intel / Apple Silicon）

### 1. 安装 Homebrew 和运行环境

如果尚未安装 Homebrew，先按 [brew.sh](https://brew.sh/) 的官方说明安装。然后执行：

```bash
xcode-select --install
brew install python@3.11 node
```

检查：

```bash
node --version
npm --version
```

### 2. 创建虚拟环境并安装项目

在项目根目录执行：

```bash
$(brew --prefix python@3.11)/bin/python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[web]"
npm --prefix frontend ci
npm --prefix frontend run build
```

上述写法同时适用于 Apple Silicon 和 Intel Mac，不要求你记住 Homebrew 的安装路径。

### 3. 启动

```bash
.venv/bin/python -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756
```

在 Safari、Chrome 或 Edge 中打开 **http://127.0.0.1:8756**。

---

## 四、其他 Linux

### Fedora / RHEL 系

```bash
sudo dnf install -y python3 python3-pip nodejs npm gcc gcc-c++ make
```

### Arch Linux 系

```bash
sudo pacman -Syu --needed python python-pip nodejs npm base-devel
```

随后统一使用 Linux 安装路径：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[web]"
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m uvicorn astock_hub.webapi:app --host 127.0.0.1 --port 8756
```

先检查 `python3 --version` 和 `node --version`，版本过低时需要另行安装新版本。

> ARM Linux、树莓派和 NAS 能否直接安装，取决于 Python 依赖是否提供对应架构的安装包，目前未做专项验证。

---

## 五、云服务器安装与安全访问

> 第一次部署云服务器时，建议先阅读[免费版云服务器安装教程（新手详细版）](CLOUD_DEPLOYMENT.md)，其中包含安全组、systemd、历史数据、完整验收和常见报错的逐步说明。

推荐使用 **Ubuntu LTS**，安装步骤与上方 Ubuntu 完全相同。

为了安全，建议保持 `--host 127.0.0.1`，不要直接把 8756 端口开放到公网。需要从自己的电脑查看时，可使用 SSH 隧道：

```bash
ssh -L 8756:127.0.0.1:8756 用户名@服务器IP
```

保持 SSH 连接，在自己电脑浏览器打开 **http://127.0.0.1:8756**。

当前采用**机主单用户模式，无内置访问控制**。公网部署必须自行增加：

- HTTPS
- 身份认证
- 访问控制与速率限制
- 防火墙规则

---

## 六、手机、平板和局域网访问

手机和平板不需要安装 Python。后端运行在电脑或服务器上，手机只负责打开网页。

Windows 上显式开启局域网访问：

```powershell
$env:ASTOCK_HUB_HOST="0.0.0.0"
powershell -ExecutionPolicy Bypass -File scripts\start-web.ps1
```

Linux / macOS 上：

```bash
.venv/bin/python -m uvicorn astock_hub.webapi:app --host 0.0.0.0 --port 8756
```

然后：

1. 确保手机和电脑在**同一个可信局域网**。
2. 在防火墙中仅对可信局域网放行 8756。
3. 手机浏览器打开 `http://电脑局域网IP:8756`。

**不要把该地址直接暴露到公网。**

---

## 七、初始化全市场数据（按需）

以下功能需要本地全市场历史数据：

- 实时看板的市场宽度、涨跌停和情绪统计
- 主线、板块轮动与部分复盘内容
- 内置技术条件的全市场策略扫描

如果已经安装并授权 `hithink-finance` CLI，可按其官方说明初始化：

```bash
hithink-finance data init
hithink-finance data sync
```

程序默认读取：

- `data/hithink_cache/`：名称、日历、概念映射与历史面板缓存
- `data/market/`：全市场日线 Parquet 数据

已有本地数据时，可放入对应目录。运行数据已被 `.gitignore` 排除，不要提交到仓库。

> 没有历史库时，程序会尝试通过可用公开源补建缓存，但首次大窗口计算可能较慢。基础个股查询不依赖这一步。

---

## 八、可选配置

复制配置模板：

**Windows PowerShell**

```powershell
Copy-Item config.example.toml config.toml
```

**Linux / macOS**

```bash
cp config.example.toml config.toml
```

更推荐直接在面板中配置：

- **数据源**：填写自己的行情服务 Key，并测试连接
- **大模型**：配置自己的模型接口，启用可选 AI 功能
- **推送设置**：配置自己的通知渠道，确认无误后再打开开关

`config.toml`、`.env` 和 `data/` 均已被 `.gitignore` 排除。

---

## 九、安装后验证

```bash
# Linux / macOS
.venv/bin/python -m pytest tests -q
```

```powershell
# Windows PowerShell
.venv\Scripts\python.exe -m pytest tests -q
```

冒烟测试全部通过后，再检查：

1. 浏览器能打开首页。
2. 搜索一只个股，行情与 K 线能够显示。
3. 已初始化历史数据时，看板和策略选股能够返回结果。
4. 未配置大模型时，基础行情功能不受影响。

## 常见问题

### `python` 或 `py` 不是内部或外部命令

Python 未安装或未加入 PATH。重新安装并勾选 PATH 选项，然后关闭并重开终端。

### 创建虚拟环境时提示 `ensurepip is not available`

Ubuntu / Debian 需要安装：

```bash
sudo apt install -y python3-venv
```

### Node.js 版本过低

从 [Node.js 官网](https://nodejs.org/)安装当前 LTS，再重新打开终端执行 `node --version` 确认。

### 页面显示“后端未连接”

确认 Uvicorn 仍在运行，并检查是否使用同一端口。生产模式不需要另外启动 Vite。

### 修改前端后页面没有变化

重新构建并重启后端：

```bash
npm --prefix frontend run build
```

生产模式读取 `frontend/dist`，不是 `frontend/src`。

### 端口被占用

把启动命令和浏览器地址中的 `8756` 一起改为其他空闲端口，例如 `8761`。

### 是否必须配置大模型

不是。AI 问答、AI 摘要和部分复盘生成需要模型配置；基础行情、图表和本地数据功能不依赖大模型。
