# Bili Insight

Bili Insight 是面向本地单用户或可信环境的 Bilibili 视频解析、在线播放预览、分析与下载工具。当前支持普通 BV/AV 投稿以及番剧 `ss/ep` 链接，提供匿名优先和用户主动启用 Cookie 登录态两种能力路径，支持媒体规格选择、任务进度、音视频处理、分析结果及产物管理。前端使用 Vue 3，后端使用 FastAPI，浏览器预览使用 Shaka Player，媒体处理依赖 FFmpeg。

本项目只用于处理使用者有权访问和使用的内容，不绕过付费、DRM、验证码、平台风控或其他访问控制。Cookie 等同账号会话凭据，请勿分享、提交到 Git 或发送给第三方。

主要能力：

- 接受完整 Bilibili URL、裸 BV/AV 号以及裸 `ss28747`、`ep733316` 等番剧标识；`ep` 链接默认选中对应剧集。
- 匿名解析可直接使用；上传 Cookie JSON 后可按登录或大会员的实际权益重新解析清晰度与音频规格。
- 选择视频和音频规格后可先在页面内播放，再决定是否创建下载任务；不支持浏览器解码的 HEVC、AV1、HDR 等规格仍可下载，并会显示兼容性提示。
- 桌面端保留固定左侧导航，内容区使用可用宽度并尽量在一屏内组织；长表格和视频工作区使用内部滚动。手机端使用顶部状态区、底部导航、卡片和抽屉布局。

## 系统结构

```text
浏览器 / Shaka Player ── HTTP / SSE / Range ── Nginx :8080 ── FastAPI :8000
                                                               ├── SQLite
                                                               ├── 同源 DASH 预览代理 ── Bilibili CDN
                                                               ├── Bilibili 元数据 / PGC 上游
                                                               └── FFmpeg / 分析任务
```

Docker 默认只把 Nginx 发布到 `127.0.0.1`。FastAPI 仅位于 Compose 内部网络，不直接暴露主机端口。运行数据和 Cookie 加密密钥位于两个独立命名卷中。

## 环境要求

本机开发需要：

- Python 3.12 或更新的 Python 3 版本
- Node.js 22 与 npm 10+
- FFmpeg 与 FFprobe
- Windows PowerShell 5.1+，或支持 POSIX shell 与 Make 的系统

容器部署需要 Docker Engine 24+ 与 Docker Compose 2.24+，无需在主机单独安装 Python、Node.js 或 FFmpeg。

## Docker Compose 部署

1. 复制无敏感信息的环境模板：

   ```powershell
   Copy-Item .env.example .env
   ```

   Linux/macOS 使用 `cp .env.example .env`。

2. 构建并启动：

   ```powershell
   docker compose up --detach --build --wait
   ```

3. 打开 <http://127.0.0.1:8080>。若修改了 `.env` 中的 `WEB_PORT`，使用相应端口。

4. 验证端到端就绪状态：

   ```powershell
   curl.exe --fail http://127.0.0.1:8080/healthz
   ```

停止服务但保留数据库、产物和密钥：

```powershell
docker compose down
```

不要使用 `docker compose down --volumes`，除非已经确认要永久删除全部应用数据和本机 Cookie 解密能力。

首次启动时，后端会在独立的 `bili-insight-secrets` 命名卷中生成 Fernet 密钥，密钥值不会写入仓库、镜像、`.env` 或日志。业务数据位于 `bili-insight-runtime` 命名卷；启动脚本会将数据库迁移到 `0005_stream_preview_metadata` 或之后的当前 head。

## 本机开发

Windows：

```powershell
.\scripts\bootstrap.ps1
.\scripts\start-dev.ps1
```

如果系统中 `python` 不是目标解释器，可执行 `.\scripts\bootstrap.ps1 -Python py`，或传入 Python 可执行文件的完整路径。

Linux/macOS：

```bash
make bootstrap
make dev
```

开发地址为 <http://127.0.0.1:5173>，Vite 将 `/api` 同源代理到 <http://127.0.0.1:8000>。开发脚本会创建被 Git 忽略的 `runtime/` 目录，执行 Alembic 迁移，并在退出时停止后端子进程。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `make test` | 执行后端 Pytest 与前端 Vitest |
| `make lint` | 执行 Ruff、mypy、ESLint 和 TypeScript 检查 |
| `make build` | 编译 Python 模块并生成前端生产构建 |
| `make compose-config` | 验证 Compose 配置 |
| `make docker-build` | 构建生产镜像 |
| `make docker-up` | 构建并启动容器，等待健康检查 |
| `make docker-logs` | 跟踪容器日志 |

也可以直接在 `backend/` 执行 `python -m pytest`，在 `frontend/` 执行 `npm run lint`、`npm run typecheck`、`npm run test`、`npm run test:e2e` 和 `npm run build`。Playwright 首次运行前执行 `npx playwright install chromium webkit firefox`；端到端测试使用测试目录内的脱敏固定 API route fixture，不访问真实 Bilibili，也不读取本机 Cookie 文件。

## 配置

根目录 `.env.example` 是 Docker Compose 的无秘密模板。常用项如下：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEB_PORT` | `8080` | 绑定到主机回环地址的 Web 端口 |
| `APP_DATABASE_URL` | 容器内 SQLite | SQLAlchemy 异步数据库连接串 |
| `APP_LOG_LEVEL` | `INFO` | 后端日志级别 |
| `APP_LOG_JSON` | `true` | 容器中输出结构化 JSON 日志 |
| `APP_METADATA_CACHE_TTL_SECONDS` | `1200` | 元数据缓存时间 |
| `APP_STREAM_CACHE_TTL_SECONDS` | `300` | 短期媒体规格缓存时间 |
| `APP_UPSTREAM_TIMEOUT_SECONDS` | `20` | 上游总超时秒数 |
| `APP_UPSTREAM_RETRIES` | `2` | 上游有限重试次数 |
| `APP_COOKIE_UPLOAD_MAX_BYTES` | `1048576` | Cookie JSON 上传上限 |

容器固定使用 `APP_NETWORK_MODE=trusted_proxy`，其含义是后端只信任同一 Compose 网络中的本地网关；这不是可直接暴露到局域网或公网的模式。完整变量和运维边界见 [部署运维说明](docs/DEPLOYMENT.md)。

### 在线播放预览

详情页会使用当前选择的视频流、音频流和身份策略创建短期预览会话。前端动态加载 Shaka Player，并关闭自动清晰度切换，确保播放的就是用户当前选中的规格。

- `POST /api/v1/previews` 创建会话，`GET /api/v1/previews/{id}/manifest.mpd` 返回静态 DASH SegmentBase MPD。
- `GET`/`HEAD /api/v1/previews/{id}/media/{video|audio}` 通过同源 Range 代理读取媒体，`DELETE /api/v1/previews/{id}` 主动释放会话。
- MPD 只包含应用内部相对路径，Bilibili 签名媒体 URL 和 Cookie 不会返回浏览器；上游响应头经过白名单过滤后仅保留 Range 播放所需字段。
- 默认空闲 TTL 为 30 分钟，持续访问会滑动续期；绝对生命周期最多 6 小时。进程内最多保留 32 个会话，超过后淘汰最久未访问会话。
- 单次 Range 响应最多 64 MiB，上游媒体请求并发最多 8。登录态清除时会同步删除所有登录预览；后端重启会结束全部预览会话，但不影响下载任务和已生成产物。
- CDN 地址失效时会按同一规格强制刷新一次。浏览器无法解码所选编码时，界面会建议改选 H.264 + AAC，原规格仍可用于下载。

### 可选本地模型

FFprobe、响度/静音、镜头、关键帧、字幕导出和可复现的证据摘要属于核心安装。ASR 与 OCR 使用较大的可选运行时：

```powershell
# 只安装 ASR
python -m pip install -e ".\backend[analysis-asr]"

# CPU 环境同时安装 ASR 与 OCR
python -m pip install -e ".\backend[analysis]"
```

Docker 可在 `.env` 中设置 `BACKEND_EXTRAS=analysis-asr`、`analysis-ocr` 或 `analysis` 后重新构建。ASR 首次使用可能下载所选 faster-whisper 模型；OCR 使用 PaddleOCR/PaddlePaddle。缺少可选依赖时，诊断页会明确显示能力不可用，其他下载、媒体分析、字幕和本地摘要能力仍可正常工作。GPU OCR 需要按 PaddlePaddle 官方兼容矩阵单独安装匹配 CUDA 的 `paddlepaddle-gpu`，不能与 CPU wheel 混装。

## 健康检查

- `/nginx-health`：仅检查静态网关进程。
- `/api/v1/health`：后端存活检查，不依赖 Cookie，也不返回敏感配置。
- `/api/v1/health/ready`：检查数据库、目录和 FFmpeg 等必要依赖。
- `/healthz`：网关转发到后端就绪检查，适合部署系统探针。

Compose 先等待后端就绪，再启动前端网关。健康检查失败时先运行 `docker compose ps`，再查看 `docker compose logs --tail=200 backend`；日志仍应按敏感数据处理，不要公开上传完整诊断内容。

## 数据与备份

- `bili-insight-runtime`：SQLite、产物、临时状态和应用日志。
- `bili-insight-secrets`：Cookie 认证加密主密钥。
- 本机开发：根目录 `runtime/`，已由 `.gitignore` 和 `.dockerignore` 排除。

“历史保留时间”只清除到期的任务、分析结果和视频上下文；视频、音频、字幕、转写及分析报告等用户可见文件会转为不含原任务元数据的受管保留文件。文件是否自动删除由独立的“产物清理周期”控制，用户也可在产物页手动彻底删除。

备份必须同时覆盖业务卷与密钥卷，但应作为两份受控敏感备份分别保存。缺少密钥卷时，已加密保存的 Cookie 无法恢复；这不会影响已经生成的媒体产物。具体停机、备份和恢复步骤见 [部署运维说明](docs/DEPLOYMENT.md)。

## 安全与合规

- 真实 `*.cookies.json`、`.env`、运行目录、下载、临时文件和密钥均被版本控制与 Docker 构建上下文排除。
- Cookie 上传后不由前端回显，服务端使用 CookieJar 的域和 path 规则，并只向允许的 Bilibili 域发送。
- 在线预览只向浏览器暴露同源短期会话地址；媒体代理逐次校验公网 DNS、固定连接 IP 与 TLS SNI，不把 Cookie 发送给媒体 CDN。
- 默认只监听回环地址。局域网或公网开放必须增加 HTTPS、应用鉴权、访问速率限制以及严格的代理信任边界。
- Nginx 为页面设置 CSP、禁止嵌入、MIME 嗅探防护和最小权限策略；SSE 与 Range 下载不启用响应缓冲。
- CI 不访问真实 Bilibili 登录态，不允许真实 Cookie 或固定私钥进入仓库。

请在处理账号凭据或开放网络访问前阅读 [安全说明](docs/SECURITY.md)。产品需求和验收依据见 [PRD](docs/PRD.md)。
