<div align="center">
  <img src="./docs/assets/bili-insight-banner.svg" alt="Bili Insight Banner" width="100%" />
  <h1>Bili Insight</h1>
  <p>面向本地单用户与可信环境的 Bilibili 视频解析、在线播放、分析与下载工作台</p>
  <p>支持普通 BV/AV 投稿与番剧 SS/EP，匿名优先，也可使用用户主动上传的 Cookie 获取账号实际拥有的媒体规格。</p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12" />
    <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/Vue-3.5-4FC08D?style=for-the-badge&logo=vuedotjs&logoColor=white" alt="Vue 3" />
    <img src="https://img.shields.io/badge/Element_Plus-2.10-409EFF?style=for-the-badge&logo=element&logoColor=white" alt="Element Plus" />
    <img src="https://img.shields.io/badge/ECharts-6-AA344D?style=for-the-badge&logo=apacheecharts&logoColor=white" alt="ECharts 6" />
  </p>
  <p>
    <img src="https://img.shields.io/badge/TypeScript-5.8-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript 5.8" />
    <img src="https://img.shields.io/badge/Shaka_Player-5.1-202124?style=flat-square&logo=googlechrome&logoColor=white" alt="Shaka Player 5.1" />
    <img src="https://img.shields.io/badge/SQLite-3-003B57?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite 3" />
    <img src="https://img.shields.io/badge/FFmpeg-required-007808?style=flat-square&logo=ffmpeg&logoColor=white" alt="FFmpeg" />
    <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker Compose" />
  </p>
  <p>
    <a href="#项目亮点">项目亮点</a>
    ·
    <a href="#功能概览">功能概览</a>
    ·
    <a href="#快速开始">快速开始</a>
    ·
    <a href="#安全边界">安全边界</a>
    ·
    <a href="#项目文档">项目文档</a>
  </p>
</div>

> [!IMPORTANT]
> 本项目只用于处理使用者有权访问和使用的内容，不绕过付费、DRM、验证码、平台风控或其他访问控制。Cookie 等同账号会话凭据，请勿分享、提交到 Git 或发送给第三方。

## 项目亮点

- **匿名优先**：无需登录即可解析公开媒体；仅在用户明确选择后使用已校验的 Cookie 登录态。
- **投稿与番剧统一工作流**：支持完整 Bilibili URL、裸 BV/AV 号、`ss28747`、`ep733316` 等标识，EP 链接会自动定位对应剧集。
- **先播放再下载**：选择视频清晰度和音轨后，可预览完整音视频，也可单独试听所选音轨，再决定是否创建下载任务。
- **真实媒体能力**：分别展示分辨率、帧率、编码、HDR、码率、音频和预估大小，不把理论档位当作可用流。
- **媒体与内容分析**：提供 FFprobe、响度、频谱、镜头、关键帧、字幕、ASR、OCR 和证据摘要能力。
- **应用安全与任务复用**：本机管理员登录保护业务接口；相同下载/分析请求自动复用并按视频聚合展示。
- **任务与产物管理**：任务支持单项/批量删除，最近解析保留简洁的单项删除；产物按视频折叠聚合，并保留批量下载、批量删除与清理能力。
- **桌面与移动端适配**：桌面端使用全宽单屏工作区，移动端使用底部导航、卡片、抽屉和安全区适配。
- **凭据与媒体地址隔离**：Cookie、签名 URL 和本地绝对路径不会回显到前端或写入普通日志。

## 技术栈

<table>
  <tr>
    <td align="center" width="25%">
      <strong>前端应用</strong><br /><br />
      Vue 3 · TypeScript<br />Element Plus · Pinia<br />Vue Router · Vite
    </td>
    <td align="center" width="25%">
      <strong>后端服务</strong><br /><br />
      FastAPI · Pydantic<br />SQLAlchemy · Alembic<br />HTTPX · SQLite
    </td>
    <td align="center" width="25%">
      <strong>媒体处理</strong><br /><br />
      Shaka Player · DASH<br />FFmpeg · FFprobe<br />SSE · HTTP Range
    </td>
    <td align="center" width="25%">
      <strong>质量保障</strong><br /><br />
      Pytest · Vitest<br />Playwright · Ruff<br />mypy · ESLint
    </td>
  </tr>
</table>

## 功能概览

### 链接解析与登录态

- 支持 `BV`、`AV`、`SS`、`EP` 及对应完整 HTTPS 链接。
- 自动清理 `spm_id_from`、`vd_source` 等无关跟踪参数，保留分 P 定位参数。
- 支持“自动（优先匿名）”“仅匿名”“使用登录态”三种解析策略。
- 设置页可上传浏览器导出的 Cookie JSON，校验登录与大会员状态，并支持替换、重新校验和彻底清除。
- Cookie 可仅在当前会话使用，也可使用本机密钥认证加密后保存。
- 首次访问先创建唯一的本机管理员账号；应用管理员会话与 Bilibili Cookie 登录态相互独立。

### 清晰度、预览与下载

- 同一清晰度下分别展示 H.264、HEVC、AV1 等编码，以及 AAC、FLAC、杜比等音轨。
- 支持最佳画质、最佳兼容、最小体积、仅音频和自定义选择。
- “下载音频”入口支持 M4A 原始封装、MP3 与 FLAC 转码，产物类型明确标记为音频。
- “试听音频”入口使用短期同源 DASH 会话播放当前所选音轨，无需先下载完整文件。
- 使用 Shaka Player 播放当前选择的 DASH 视频与音频，不自动切换到其他清晰度。
- 浏览器不支持 HEVC、AV1 或 HDR 时给出兼容性提示，原规格仍可下载。
- 下载任务开始前刷新并验证临时媒体地址，支持 DASH 音视频合并、封装与可选转码。

### 分析与产物

- 技术分析：媒体参数、响度、静音、频谱、镜头、关键帧与时间线图表。
- 内容分析：公开字幕、ASR、OCR、章节、关键词和带证据定位的摘要。
- 任务中心：按视频聚合相关任务，查看阶段、进度、速度和失败原因；终态任务支持单项/批量删除，已有产物会转为可继续管理的受管保留文件。
- 最近解析：每条记录提供独立删除入口；有关联任务或分析的数据会被安全阻止删除。
- 产物中心：默认按视频标题折叠展示产物数量、总大小、类型和最新时间，展开后可预览、保存、单项/批量删除并跳转官方源视频。

## 系统架构

```text
浏览器 / Shaka Player
        │ REST · SSE · 同源 DASH Range
        ▼
Nginx :8080 ─────────────── FastAPI :8000
                                  │
                ┌─────────────────┼──────────────────┐
                ▼                 ▼                  ▼
          Bilibili Provider   Preview Service   Download / Analysis
                │            短期 MPD 与代理       FFmpeg / Models
                └─────────────────┼──────────────────┘
                                  ▼
                         SQLite · Artifacts
```

Docker 默认只把 Nginx 发布到 `127.0.0.1`；需要手机访问时可显式改为局域网监听。FastAPI 始终位于 Compose 内部网络，不直接暴露主机端口。运行数据与 Cookie 加密密钥分别存放在独立命名卷中。

## 快速开始

### Docker Compose

环境要求：Docker Engine 24+、Docker Compose 2.24+。

```powershell
Copy-Item .env.example .env
docker compose up --detach --build --wait
```

### 使用 GHCR 镜像

发布后的镜像地址为：

```text
ghcr.io/junwan666/bili-insight-backend:latest
ghcr.io/junwan666/bili-insight-frontend:latest
```

将 `.env` 中的镜像变量改为：

```dotenv
BACKEND_IMAGE=ghcr.io/junwan666/bili-insight-backend:latest
FRONTEND_IMAGE=ghcr.io/junwan666/bili-insight-frontend:latest
```

然后拉取并启动，不再本地构建：

```bash
docker compose pull
docker compose up --detach --no-build --wait
```

GHCR 包可能继承 GitHub 仓库的私有可见性。首次拉取私有包前需要使用拥有该仓库访问权限的 GitHub Personal Access Token 登录：

```bash
echo "$CR_PAT" | docker login ghcr.io -u JunWan666 --password-stdin
```

### 一键下载 Compose 文件

下面的命令会从 GitHub `v1.2.1` Release 对应的源码标签下载 Compose 文件和 GHCR 配置，不需要克隆整个仓库。当前仓库为私有仓库，先准备一个同时拥有仓库读取权限和 `read:packages` 权限的 GitHub Token：

Linux/macOS：

```bash
export GITHUB_TOKEN=ghp_your_token
curl -H "Authorization: Bearer ${GITHUB_TOKEN}" -fsSL https://raw.githubusercontent.com/JunWan666/bili-insight/v1.2.1/docker-compose.yml -o docker-compose.yml
curl -H "Authorization: Bearer ${GITHUB_TOKEN}" -fsSL https://raw.githubusercontent.com/JunWan666/bili-insight/v1.2.1/ghcr-compose.env -o .env
docker login ghcr.io
docker compose pull
docker compose up --detach --no-build --wait
```

Windows PowerShell：

```powershell
$headers = @{ Authorization = "Bearer $env:GITHUB_TOKEN" }
Invoke-WebRequest -UseBasicParsing -Headers $headers https://raw.githubusercontent.com/JunWan666/bili-insight/v1.2.1/docker-compose.yml -OutFile docker-compose.yml
Invoke-WebRequest -UseBasicParsing -Headers $headers https://raw.githubusercontent.com/JunWan666/bili-insight/v1.2.1/ghcr-compose.env -OutFile .env
docker login ghcr.io
docker compose pull
docker compose up --detach --no-build --wait
```

默认只监听本机；需要手机访问时，将下载后的 `.env` 中 `WEB_HOST` 改为 `0.0.0.0`，再执行 `docker compose up --detach --no-build --force-recreate --wait`。

Linux/macOS：

```bash
cp .env.example .env
docker compose up --detach --build --wait
```

启动后访问：

- 应用首页：<http://127.0.0.1:8080>
- 健康检查：<http://127.0.0.1:8080/healthz>

停止服务但保留数据库、产物与密钥：

```bash
docker compose down
```

> [!CAUTION]
> 不要使用 `docker compose down --volumes`，除非已经确认要永久删除全部应用数据和本机 Cookie 解密能力。

### 本机开发

环境要求：Python 3.12+、Node.js 22+、npm 10+、FFmpeg 与 FFprobe。

Windows：

```powershell
.\scripts\bootstrap.ps1
.\scripts\start-dev.ps1
```

Linux/macOS：

```bash
make bootstrap
make dev
```

开发地址为 <http://127.0.0.1:5173>，Vite 会将 `/api` 同源代理到 <http://127.0.0.1:8000>。

### 局域网访问

Docker 模式在 `.env` 中设置：

```dotenv
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

重新创建前端网关后，同一局域网内的设备可访问 `http://<电脑局域网IP>:8080`：

```bash
docker compose up --detach --force-recreate frontend
```

Windows 开发模式也可直接启动局域网监听：

```powershell
.\scripts\start-dev.ps1 -HostAddress 0.0.0.0
```

Linux/macOS 开发模式：

```bash
VITE_DEV_HOST=0.0.0.0 make dev
```

局域网模式只暴露 Nginx 或 Vite，FastAPI 仍保持内部访问。请仅在可信家庭或办公网络使用，使用结束后把 `WEB_HOST` 改回 `127.0.0.1`；若手机无法连接，请检查主机防火墙是否允许对应的 TCP 端口。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `make test` | 执行后端 Pytest 与前端 Vitest |
| `make lint` | 执行 Ruff、mypy、ESLint 和 TypeScript 检查 |
| `make build` | 编译 Python 模块并生成前端生产构建 |
| `make compose-config` | 验证 Docker Compose 配置 |
| `make docker-build` | 构建前后端生产镜像 |
| `make docker-up` | 构建并启动容器，等待健康检查 |
| `make docker-logs` | 跟踪容器日志 |

前端端到端测试使用脱敏固定 API fixture，不访问真实 Bilibili，也不会读取本机 Cookie 文件。

## 项目结构

```text
bili-insight/
├── backend/                  # FastAPI、Provider、任务与媒体服务
├── frontend/                 # Vue 3 响应式 Web 应用
├── docker/                   # 前后端镜像与 Nginx 配置
├── docs/                     # PRD、部署、安全与实施状态文档
├── scripts/                  # 初始化、开发启动与凭据扫描脚本
├── docker-compose.yml        # 本地生产化编排
└── README.md
```

## 可选分析能力

基础媒体探测、响度/静音、镜头、关键帧、字幕导出与证据摘要属于核心安装。ASR 与 OCR 需要额外运行时：

```powershell
# 只安装 ASR
python -m pip install -e ".\backend[analysis-asr]"

# 同时安装 ASR 与 OCR
python -m pip install -e ".\backend[analysis]"
```

Docker 可在 `.env` 中设置 `BACKEND_EXTRAS=analysis-asr`、`analysis-ocr` 或 `analysis` 后重新构建。ASR 首次运行可能下载模型；GPU OCR 需要按 PaddlePaddle 官方兼容矩阵安装匹配 CUDA 的运行时。

## 安全边界

- 真实 `*.cookies.json`、`.env`、运行目录、下载、临时文件和密钥均被 Git 与 Docker 构建上下文排除。
- Cookie 上传后不由前端回显，只按域和 Path 规则发送到允许的 Bilibili 服务。
- 在线预览仅向浏览器暴露同源短期会话地址；MPD 不包含 Cookie 或 Bilibili 签名媒体 URL。
- 媒体代理会校验公网 DNS、固定连接 IP、TLS SNI、CDN 域名、MIME、Range 与响应长度。
- 默认只监听回环地址；应用自带本机管理员鉴权，开放到长期局域网或公网前仍必须增加 HTTPS、限流和严格代理边界。
- CI、固定测试数据、截图和诊断导出不得包含真实 Cookie、账号标识或永久私钥。

## 配置与数据

根目录 `.env.example` 是无敏感信息的 Compose 配置模板。完整变量说明见 [部署运维说明](docs/DEPLOYMENT.md)。

| 数据位置 | 内容 |
| --- | --- |
| `bili-insight-runtime` | SQLite、产物、临时状态和应用日志 |
| `bili-insight-secrets` | Cookie 认证加密主密钥 |
| 本机 `runtime/` | 开发环境数据库、产物、临时文件和日志 |

备份时必须同时覆盖业务卷与密钥卷，并作为两份受控敏感备份分别保存。缺少密钥卷时，已加密保存的 Cookie 无法恢复，但不会影响已经生成的媒体产物。

## 项目文档

- [产品需求文档](docs/PRD.md)：产品范围、业务流程、验收标准与迭代计划。
- [部署运维说明](docs/DEPLOYMENT.md)：环境变量、Docker、本机开发、备份与恢复。
- [安全说明](docs/SECURITY.md)：Cookie、媒体代理、网络部署与威胁边界。
- [实施状态](docs/IMPLEMENTATION_STATUS.md)：当前功能完成情况、测试基线与已知边界。

## 合规说明

本项目不提供绕过付费、DRM、验证码、地区限制或平台风控的能力，不提供 Cookie 分享或账号交易功能。下载与使用行为应遵守 Bilibili 平台条款以及所在地版权法律，使用者需自行确认对目标内容拥有相应访问和使用权限。
