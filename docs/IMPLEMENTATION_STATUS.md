# 实施与验收状态

本文档依据 `AGENTS.md` 与 `docs/PRD.md` 维护。状态为 `verified` 表示已具备源码、自动化测试或真实运行证据，并在 2026-07-16 的当前交付验收中通过。

## 当前结论

当前版本达到本地单用户/可信环境的 Production Ready 标准。`v1.2.3` 在单屏解析工作台中加入低干扰媒体信号装饰，将最近解析收敛为桌面四列起步的高密度信息卡，并把设置分组迁入可展开的侧栏二级菜单；移动端使用紧凑分组选择器保持全部设置可达。

## 当前阶段

| 阶段 | 状态 | 完成证据 |
| --- | --- | --- |
| 仓库与可运行骨架 | verified | Python/Node/FFmpeg 环境检查、开发脚本、CI、生产构建和健康检查通过 |
| Provider、Cookie 与解析 | verified | 原 BV/AV 能力保持通过；新增 `ss/ep`、Season/Episode、PGC 匿名/登录/会员规格、Provider 隔离、特别篇稳定性和 Cookie 清除回归通过 |
| 下载、任务与产物 | verified | DASH 下载、合并、FFprobe、暂停恢复、重试、任务批量删除、最近解析安全删除、产物折叠分组、Range 与删除测试通过 |
| 媒体与内容分析 | verified | 基础、媒体、音频、字幕、ASR/OCR 适配、镜头、摘要、编辑和导出链路通过 |
| 在线播放预览 | verified | Shaka 组件、静态 SegmentBase MPD、视频与独立音轨预览、同源 Range 代理、TTL/限额、强制刷新、登录态清理、安全故障注入及真实 4K 播放通过 |
| Vue 页面与移动端 | verified | 单屏解析工作台自然装饰、最近解析高密度网格、桌面设置二级菜单、平板/手机紧凑分组选择器、五项移动导航和预览弹窗通过完整跨浏览器矩阵 |
| Docker、运维与安全 | verified | 最新生产镜像重建、`0006_application_auth` 实际迁移、健康检查、CDN 端口窄放行和预览代理安全测试通过 |
| 全量测试与 PRD 审计 | verified | 原 AC 与新增 AC-PARSE-06、AC-PREVIEW、AC-MOBILE-06 均具备自动化及真实运行证据 |

## PRD 验收追踪

| 验收编号 | 状态 | 验证证据 |
| --- | --- | --- |
| AC-PARSE-01 | verified | URL 工具、Provider、API 与 E2E 验证 BV/AV 及跟踪参数清理 |
| AC-PARSE-02 | verified | 固定多 P 响应、指定 `p`、详情切换与批量抽屉测试 |
| AC-PARSE-03 | verified | HTTPS/域名白名单、私网地址、DNS 重绑定和媒体 URL 固定测试 |
| AC-PARSE-04 | verified | 删除、权限、风控、网络和不支持类型的错误归一化/API 测试 |
| AC-PARSE-05 | verified | 自动/仅匿名请求 Cookie 隔离测试与 E2E 请求断言 |
| AC-PARSE-06 | verified | 裸/完整 ss/ep、Season/Episode、默认剧集、特别篇稳定性、PGC 固定响应及 provider+aid 隔离测试 |
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
| AC-PREVIEW-01 | verified | Shaka 按需加载并固定所选规格；真实 4K H.264 + AAC 达到 `readyState=4`、时间推进、暂停和拖动通过 |
| AC-PREVIEW-02 | verified | MPD 仅含内部相对路径；真实与固定测试均确认 API 不返回上游签名 URL 或 Cookie |
| AC-PREVIEW-03 | verified | GET/HEAD/206/416、Range 上限、DNS/IP/SNI、MIME、Content-Range、响应长度、并发和 CDN `4483` 窄放行测试 |
| AC-PREVIEW-04 | verified | 主动删除、滑动/绝对过期、会话淘汰、地址刷新、竞态清理和清除登录预览测试 |
| AC-PREVIEW-05 | verified | 视频与音轨浏览器能力检查、H.264 + AAC 建议、音轨安全降级和“不影响下载”组件测试 |
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
| AC-MOBILE-06 | verified | 手机 Tooltip 遮挡播放按钮问题已修复，Chromium/WebKit 完整移动矩阵通过 |
| AC-HISTORY-01 | verified | 终态任务单项/批量删除、活动任务 409、关联产物受管保留和分析记录清理测试 |
| AC-HISTORY-02 | verified | 最近解析单项/批量删除、关联任务冲突和媒体流缓存清理测试 |
| AC-HISTORY-03 | verified | 产物按视频默认折叠、展开明细、整组选中、桌面/移动删除及 44px 触控门禁 |
| AC-SEC-01 | verified | SSRF、路径穿越、命令注入、文件名和 XSS 纯文本渲染测试 |
| AC-SEC-02 | verified | CookieJar 域/path/secure 语义及 Bilibili 允许域发送测试 |
| AC-SEC-03 | verified | Cookie 文件忽略、仓库扫描、日志/诊断/数据库脱敏和 E2E 假凭据检查 |
| AC-SEC-04 | verified | local/trusted_proxy/public 启动门禁、API key 和 CORS 配置测试 |

## 本轮增量验证

| 门禁 | 当前结果 |
| --- | --- |
| 后端 Pytest | 492 passed，应用覆盖率 86.63%，高于 85% 门槛 |
| 后端静态检查 | Ruff check、Ruff format check、mypy strict 全部通过 |
| 前端单元测试 | 19 个文件、106 项 Vitest 全部通过 |
| 前端静态与构建 | ESLint、Vue/TypeScript typecheck、Vite production build 全部通过 |
| 完整 Playwright 矩阵 | 203 项全部按预期通过或跳过；Chromium 360/390/768/1440、WebKit 手机/桌面和 Firefox 桌面 0 failed |
| Docker 与迁移 | 最终源码已在 CI 中构建前后端生产镜像并完成 Compose 启动、健康与持久化验收；本机两个容器 healthy，Alembic 为 `0006_application_auth (head)` |
| 真实番剧播放 | `ss28747` 年度大会员解析得到 19 路视频、3 路音频；4K H.264 + AAC 在 Chromium 中实际播放、暂停和拖动通过 |
| 依赖与仓库安全 | Python 两组 `pip-audit` 无已知漏洞；npm 官方 registry 审计 0 vulnerabilities；仓库凭据扫描通过 |
| Compose 配置 | `docker compose --env-file .env.example config --quiet` 通过 |

## 上一交付基线测试记录

| 门禁 | 结果 |
| --- | --- |
| 后端 Pytest | 432 passed，应用分支覆盖率 87.16%，高于 85% 门槛（本轮修改前基线） |
| 后端静态检查 | Ruff check、Ruff format check、mypy strict 全部通过 |
| 前端单元测试 | 18 个文件、81 项 Vitest 全部通过（本轮修改前基线） |
| 前端静态与构建 | ESLint、Vue/TypeScript typecheck、Vite production build 全部通过 |
| 主动流探测专项 | 后端 API/健康 15 项、前端 14 项单测及桌面/移动 Chromium E2E 2 项通过 |
| Playwright 全套 | 189 项：174 passed、15 项按设备能力预期 skipped；0 failed |
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

### 番剧 PGC 与真实预览

- 样例：`ss28747`《凡人修仙传》；裸 `ss/ep` 与完整 `/bangumi/play/ss|ep...` 输入已由固定响应和 API 测试覆盖。
- `ss` 解析为 Season 与剧集列表，`ep` 默认定位对应剧集；内部 Provider 使用 `bilibili_pgc`，Season 对外标识为 `SS{season_id}`，查询按 Provider 隔离，避免与同号普通投稿 aid 冲突。
- 真实 Cookie 会话已确认登录有效且年度大会员有效。首集匿名实际为质量 ID 32（480P）；登录后返回 19 路视频、3 路音频，最高 3840×2160，并提供 H.264、HEVC、AV1 与 HDR 档位。
- 当前 PGC CDN 使用 `*.edge.mountaintoys.cn:4483`；代码仅对该精确后缀窄放行 `4483`，不会放宽到父域或其他端口。
- 选择 3840×2160、25 fps、约 9.58 Mbps 的 4K H.264 和约 214 kbps AAC 创建预览。MPD 不含绝对上游 URL、签名参数或 Cookie；视频和音频各完成 1 KiB Range，均返回 HTTP 206。
- Chromium 最终容器复验达到 `readyState=4`、`currentTime=0.542929`、时长 1204 秒、未静音且音量为 1；暂停后 `paused=true`，拖动到 5.542929 秒成功，控制台和 page error 为 0。

## 本轮 Docker 验证

- 前后端生产镜像均由最终源码构建；容器发布工作流已推送 GHCR 镜像，CI 使用同一源码完成 Compose `up --detach --no-build --wait`、健康检查和持久化重启验收。本机两个容器均为 healthy。
- 网关仅发布 `127.0.0.1:8080`；后端 `8000` 不发布到主机。
- 后端运行用户为 `uid=10001(app)`，前端运行用户为 `uid=101(nginx)`。
- 两个容器均 `cap_drop: ALL` 且启用 `no-new-privileges`；前端根文件系统只读。
- Cookie 加密密钥由独立命名卷生成，权限为 `0600 app:app`。
- Alembic 实际运行版本为 `0006_application_auth (head)`。
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
| `0005_stream_preview_metadata` | 为 `media_streams` 增加 `mime_type`、`codec_string`、初始化 Range 与索引 Range 元数据，用于生成不含上游 URL 的 SegmentBase MPD |
| `0006_application_auth` | 增加本机管理员、Argon2id 密码哈希、可撤销应用会话和 CSRF 校验字段 |

## 本轮运行页面验收

- Docker 实际管理员初始化页在 1440×900 与 390×844 下均返回 HTTP 200，`scrollWidth=clientWidth`，控制台和 page error 为 0。
- 390×844 首屏完整展示品牌、用户名、密码、确认密码和“初始化并进入”按钮，没有裁切、横向滚动或控件遮挡。
- 实际匿名详情页返回 6 路视频、3 路音频；所选视频/音频完成小范围验证，未使用登录态。
- 390×844 详情页中音频卡片、验证按钮、下载按钮和底部导航互不遮挡；Bilibili HTTP 封面会在 Provider/API 层升级为 HTTPS，实际封面宽度 1920 px，CSP 控制台错误为 0。
- 本轮 Docker 登录页视觉证据位于 `frontend/test-results/docker-login-desktop.png` 与 `docker-login-mobile.png`；该目录被 Git 忽略。
- 桌面端已移除主要页面的全局窄宽度约束，1440×900 下内容区左右间距均为 32 px；视频详情不产生页面级纵向或横向滚动，媒体规格在工作区内部滚动。
- 390×844 受保护页面的顶部状态、底部导航、卡片/抽屉和预览弹窗无横向溢出；完整 Chromium/WebKit/Firefox 七项目矩阵已通过。

## 部署边界

标准配置面向本地单用户或可信环境，并只绑定回环地址。局域网/公网部署必须按 `docs/DEPLOYMENT.md` 增加 HTTPS、应用鉴权、可信代理边界和访问速率限制。ASR、OCR 与外部视觉实体模型属于可选运行时；未安装时系统明确显示能力不可用，不影响下载、媒体分析、字幕和本地证据摘要。当前内置摘要可引用已经产生的元数据、字幕/转写、OCR 与关键帧证据，但不会声称已执行人物/对象视觉识别。

## 已知问题

**无**

## 本轮验收结果

- 完整 Playwright 七项目矩阵、最近解析四列密度门禁、设置侧栏二级菜单、手机设置选择器与保存栏层级复验均已完成。
- 当前无阻断交付项；剩余边界见下节。

## 非阻断验证边界

- 解析 P95 与大文件常驻内存尚未做独立压力基准；当前证据来自缓存/超时测试、固定 256 KiB 流式块、1 TiB 大小上限、真实下载恢复和进程 RSS watchdog。
- Safari 兼容性由 Playwright WebKit 手机/桌面项目覆盖；当前 Windows 验收环境无法运行两个正式 Safari 发布版本。
- 外部人物/对象视觉实体模型是可选运行时，当前未启用；内置摘要只输出有来源证据的投稿者元数据、文本主题、情绪措辞和关键帧结构，不伪造视觉实体识别结论。
