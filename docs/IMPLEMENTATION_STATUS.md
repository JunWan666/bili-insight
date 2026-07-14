# 实施与验收状态

本文档依据 `AGENTS.md` 与 `docs/PRD.md` 维护。状态为 `verified` 表示已具备源码、自动化测试或真实运行证据，并在 2026-07-14 的最终验收中通过。

## 当前结论

项目已达到本地单用户/可信环境的 Production Ready 标准：核心页面、API、下载与分析任务、Cookie 登录态、移动端、数据库迁移、Docker 部署和安全门禁均已验证。标准 Docker Compose 栈当前运行于 <http://127.0.0.1:8080>。

## 当前阶段

| 阶段 | 状态 | 完成证据 |
| --- | --- | --- |
| 仓库与可运行骨架 | verified | Python/Node/FFmpeg 环境检查、开发脚本、CI、生产构建和健康检查通过 |
| Provider、Cookie 与解析 | verified | 固定响应回归、真实匿名链路、授权登录链路、Cookie 清除与 DNS 固定验证通过 |
| 下载、任务与产物 | verified | DASH 下载、合并、FFprobe、暂停恢复、重试、伴随产物、Range 与删除测试通过 |
| 媒体与内容分析 | verified | 基础、媒体、音频、字幕、ASR/OCR 适配、镜头、摘要、编辑和导出链路通过 |
| Vue 页面与移动端 | verified | 六个核心页面、四种目标尺寸、触控、首屏、抽屉和跨浏览器 E2E 通过 |
| Docker、运维与安全 | verified | 双镜像构建、健康等待、非 root、只读根文件系统、密钥卷、迁移和重启持久性通过 |
| 全量测试与 PRD 审计 | verified | PRD AC-PARSE/AUTH/DL/AN/MOBILE/SEC 全部具备验证证据 |

## PRD 验收追踪

| 验收编号 | 状态 | 验证证据 |
| --- | --- | --- |
| AC-PARSE-01 | verified | URL 工具、Provider、API 与 E2E 验证 BV/AV 及跟踪参数清理 |
| AC-PARSE-02 | verified | 固定多 P 响应、指定 `p`、详情切换与批量抽屉测试 |
| AC-PARSE-03 | verified | HTTPS/域名白名单、私网地址、DNS 重绑定和媒体 URL 固定测试 |
| AC-PARSE-04 | verified | 删除、权限、风控、网络和不支持类型的错误归一化/API 测试 |
| AC-PARSE-05 | verified | 自动/仅匿名请求 Cookie 隔离测试与 E2E 请求断言 |
| AC-AUTH-01 | verified | Cookie 上传、登录/会员状态集成测试及授权本地验证 |
| AC-AUTH-02 | verified | 匿名/登录媒体能力差异 API、UI 与授权本地验证 |
| AC-AUTH-03 | verified | 过期、无效、非有限时间戳、验证异常和匿名降级测试 |
| AC-AUTH-04 | verified | API/日志脱敏、浏览器存储断言、仓库凭据扫描和诊断测试 |
| AC-AUTH-05 | verified | 清除内存、密文、数据库档案、登录流及 `authAvailable` 派生缓存测试 |
| AC-AUTH-06 | verified | API `access` 上下文及“可用但未使用/本次已使用”E2E |
| AC-DL-01 | verified | 同清晰度多编码模型、兼容提示、桌面表格和移动卡片测试 |
| AC-DL-02 | verified | 详情页选择前主动 Range 探测、任务前刷新复验、Referer、DNS 公网固定、身份隔离和真实流验证 |
| AC-DL-03 | verified | 真实匿名 DASH 音视频下载及 FFmpeg 无损合并；固定媒体回归通过 |
| AC-DL-04 | verified | FFprobe 轨道、时长和同步校验；真实产物偏差 0.529 秒 |
| AC-DL-05 | verified | 取消、暂停、继续、失败重试、原子发布和半成品隔离测试 |
| AC-DL-06 | verified | 地址刷新、有限重试、退避和重试上限故障注入测试 |
| AC-AN-01 | verified | 基础与媒体分析可独立创建、执行和查询 |
| AC-AN-02 | verified | 公开字幕优先、时间戳、来源和字幕伴随产物测试 |
| AC-AN-03 | verified | ASR/OCR 可选适配、置信度/来源、缺依赖降级及 SRT/VTT/TXT/JSON 导出测试 |
| AC-AN-04 | verified | 摘要模型/版本/参数/生成时间、免责声明和时间戳/关键帧证据测试 |
| AC-AN-05 | verified | 步骤局部失败、已成功结果保留、manifest 和警告状态测试 |
| AC-MOBILE-01 | verified | 360×800、390×844、768×1024 无页面级横向溢出 |
| AC-MOBILE-02 | verified | 手机媒体流卡片及下载/分析底部抽屉 E2E |
| AC-MOBILE-03 | verified | 移动文件选择器 Cookie 上传与清除 E2E |
| AC-MOBILE-04 | verified | 页面刷新、活动任务查询、SSE 断线轮询及 Worker 重启恢复 |
| AC-MOBILE-05 | verified | 触控目标不小于 44×44 px，关键流程使用 tap 且不依赖 hover |
| AC-SEC-01 | verified | SSRF、路径穿越、命令注入、文件名和 XSS 纯文本渲染测试 |
| AC-SEC-02 | verified | CookieJar 域/path/secure 语义及 Bilibili 允许域发送测试 |
| AC-SEC-03 | verified | Cookie 文件忽略、仓库扫描、日志/诊断/数据库脱敏和 E2E 假凭据检查 |
| AC-SEC-04 | verified | local/trusted_proxy/public 启动门禁、API key 和 CORS 配置测试 |

## 最终测试记录

| 门禁 | 结果 |
| --- | --- |
| 后端 Pytest | 432 passed，应用分支覆盖率 87.16%，高于 85% 门槛 |
| 后端静态检查 | Ruff check、Ruff format check、mypy strict 全部通过 |
| 前端单元测试 | 18 个文件、81 项 Vitest 全部通过 |
| 前端静态与构建 | ESLint、Vue/TypeScript typecheck、Vite production build 全部通过 |
| 主动流探测专项 | 后端 API/健康 15 项、前端 14 项单测及桌面/移动 Chromium E2E 2 项通过 |
| Playwright 全套 | 140 项：129 passed、11 项按设备能力预期 skipped；0 failed |
| Playwright 通道 | 本机 Chrome/Edge 40 项：34 passed、6 项桌面触控预期 skipped |
| 移动端专项 | Chromium 360/390 与 WebKit 390 共 60 项全部通过 |
| 依赖安全 | `pip-audit` 无已知漏洞；npm 官方审计 0 vulnerabilities |
| 仓库安全 | `scripts/check_repository_secrets.py` 通过；真实 Cookie 文件保持忽略 |
| Compose 配置 | `docker compose --env-file .env.example config --quiet` 通过 |

## 真实上游验证

### 匿名下载与恢复

- 样例：`BV1FYT5zkE1q`，普通单 P 视频。
- 匿名 DASH 返回 6 路视频、3 路音频，当前实际范围为 360P 至 480P。
- 选择 360P H.264 与约 51 kbps 音频执行真实下载；限速时持续上报真实字节进度。
- 任务暂停后关闭并重建应用 lifespan，状态仍为 paused；恢复后完成。
- 主媒体大小 9,978,204 bytes，FFprobe 时长 217.471 秒，相对源时长 218 秒偏差 0.529 秒；包含 H.264 视频轨和 AAC 音频轨。
- 元数据与 1,200 条弹幕伴随产物完成。
- 删除主媒体后执行 basic + media 分析，自动选择 480P 视频与分析所需音频，分析完成且临时媒体清理。
- 验收隔离目录最终删除；该链路未读取 Cookie。

### 授权登录态

- 已在隔离的本地会话中验证 Cookie 上传、会员状态、登录规格提升、真实流探测和彻底清除。
- 样例登录态返回视频质量 ID 112/80/64/32/16、音频质量 ID 30280/30232/30216；当前源最高为 1920×1080、H.264/AVC、25 fps、约 3.55 Mbps。
- 验证记录不保存 Cookie、账号标识或签名媒体 URL；清除后凭据、身份档案和登录流缓存均归零。

## Docker 验证

- 后端和前端生产镜像均由最新源码成功构建。
- Compose `up --detach --no-build --wait` 后两个容器均为 healthy。
- 网关仅发布 `127.0.0.1:8080`；后端 `8000` 不发布到主机。
- 后端运行用户为 `uid=10001(app)`，前端运行用户为 `uid=101(nginx)`。
- 两个容器均 `cap_drop: ALL` 且启用 `no-new-privileges`；前端根文件系统只读。
- Cookie 加密密钥由独立命名卷生成，权限为 `0600 app:app`。
- Alembic 当前版本为 `0004_retained_files (head)`。
- 运行卷探针在后端重启后仍存在，验证 SQLite/产物卷持久性；探针随后已删除。
- `/healthz` 与 `/api/v1/health/ready` 均返回 ready；数据库、Worker、FFmpeg 和 FFprobe 正常，存储探针同时验证目录可写性与最低磁盘余量。
- Nginx CSP、COOP、Permissions Policy、Referrer Policy、nosniff 和 frame deny 响应头生效。

## 数据库迁移

| 迁移 | 变更 |
| --- | --- |
| `d906dc4b1e71_initial_schema` | 建立认证、视频、分 P、媒体流、任务、产物和分析基础表 |
| `0002_app_settings` | 增加持久化应用设置 |
| `0003_stream_access_requirement` | 为媒体流增加 NONE/LOGIN/PREMIUM/SPECIAL 权益要求并回填旧数据 |
| `0004_retained_files` | 增加隐私历史清理后仍受管的保留文件表、索引和安全降级保护 |

## 运行页面验收

- Docker 实际页面在 1440×900 与 360×800 下均返回 HTTP 200，无横向溢出和控制台/page error。
- 360×800 首屏中：身份状态位于 y=118–150，链接输入位于 y=184–222，解析按钮位于 y=583–635，底部导航从 y=733 开始，关键操作未被遮挡。
- 实际匿名详情页返回 6 路视频、3 路音频；所选视频/音频完成小范围验证，未使用登录态。
- 390×844 详情页中音频卡片、验证按钮、下载按钮和底部导航互不遮挡；Bilibili HTTP 封面会在 Provider/API 层升级为 HTTPS，实际封面宽度 1920 px，CSP 控制台错误为 0。
- 视觉证据位于 `frontend/test-results/docker-live-home-desktop.png`、`docker-live-home-mobile.png` 与 `docker-live-streams-mobile.png`，该目录被 Git 忽略。

## 部署边界

标准配置面向本地单用户或可信环境，并只绑定回环地址。局域网/公网部署必须按 `docs/DEPLOYMENT.md` 增加 HTTPS、应用鉴权、可信代理边界和访问速率限制。ASR、OCR 与外部视觉实体模型属于可选运行时；未安装时系统明确显示能力不可用，不影响下载、媒体分析、字幕和本地证据摘要。当前内置摘要可引用已经产生的元数据、字幕/转写、OCR 与关键帧证据，但不会声称已执行人物/对象视觉识别。

## 已知问题

**无**

## 非阻断验证边界

- 解析 P95 与大文件常驻内存尚未做独立压力基准；当前证据来自缓存/超时测试、固定 256 KiB 流式块、1 TiB 大小上限、真实下载恢复和进程 RSS watchdog。
- Safari 兼容性由 Playwright WebKit 手机/桌面项目覆盖；当前 Windows 验收环境无法运行两个正式 Safari 发布版本。
- 外部人物/对象视觉实体模型是可选运行时，当前未启用；内置摘要只输出有来源证据的投稿者元数据、文本主题、情绪措辞和关键帧结构，不伪造视觉实体识别结论。
