# 部署与运维说明

## 部署模型

标准部署由 `frontend` 和 `backend` 两个容器组成。`frontend` 使用 Nginx 提供静态资源、SSE、API、同源 DASH 预览和产物下载反向代理；`backend` 运行单个 Uvicorn Worker，以保证进程内任务协调状态与短期预览会话一致。后端可以访问 Bilibili 元数据/PGC 上游和受限媒体 CDN，但没有主机端口映射。

主机默认只发布 `${WEB_HOST:-127.0.0.1}:${WEB_PORT}`。这与 PRD 的本地单用户默认模型一致，也避免因误改后端监听地址而直接暴露 Cookie 管理接口。`WEB_HOST=0.0.0.0` 可用于可信局域网内的临时移动端验收，但不会改变后端无主机端口映射的边界。

## 首次部署

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --detach --build --wait
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

启动顺序如下：

1. 后端入口创建运行目录。
2. 独立密钥卷没有有效密钥时，生成权限为仅应用用户可读的 Fernet 密钥。
3. Alembic 将数据库迁移到当前版本；本轮 schema head 为 `0006_application_auth`。
4. Uvicorn 启动，Compose 持续检查 `/api/v1/health/ready`。
5. 后端就绪后，Nginx 网关启动并发布本机端口。

若迁移、目录权限、磁盘空间或 FFmpeg 检查失败，后端不会被判定为就绪，网关也不会提前接收业务流量。

首次升级到 `0005_stream_preview_metadata` 时，`media_streams` 会增加 `mime_type`、`codec_string`、初始化 Range 起止和索引 Range 起止字段；升级到 `0006_application_auth` 时会增加本机管理员、可撤销会话和 CSRF 校验所需的表。迁移不保存上游签名 URL；已有流记录缺少这些字段时不会被错误用于在线播放，重新解析对应视频后会写入当前预览元数据。

## 更新与回滚

更新前先备份两个命名卷。获取新版本后执行：

```bash
docker compose build --pull
docker compose up --detach --wait
```

数据库迁移在后端启动前执行。回滚应用镜像前必须确认旧版本数据库模型能够读取当前迁移版本；数据库不兼容时，应使用更新前备份恢复到配套数据版本。

从 `0005` 回滚到旧镜像前尤其需要确认旧版 ORM 对新增可空列的兼容性。若必须执行 Alembic downgrade，先停止应用并备份运行卷；降级会移除预览元数据列，但不会删除下载产物。

## 备份

先停止应用，避免 SQLite 和产物在归档过程中发生变化：

```bash
docker compose down
mkdir -p backups
docker run --rm -v bili-insight-runtime:/source:ro -v "$PWD/backups:/backup" alpine:3.21 sh -c 'cd /source && tar czf /backup/runtime.tar.gz .'
docker run --rm -v bili-insight-secrets:/source:ro -v "$PWD/backups:/backup" alpine:3.21 sh -c 'cd /source && tar czf /backup/secrets.tar.gz .'
```

`secrets.tar.gz` 能解密持久化 Cookie，必须采用比普通媒体产物更严格的访问控制。恢复演练应在隔离环境中进行；恢复前保留当前卷快照，并确认目标卷为空或内容可以被覆盖。

## 健康与诊断

| 路径 | 用途 | 失败含义 |
| --- | --- | --- |
| `/nginx-health` | 网关存活 | Nginx 未启动或监听失败 |
| `/api/v1/health` | 后端存活 | Uvicorn 或应用事件循环不可用 |
| `/api/v1/health/ready` | 后端就绪 | 数据库、目录、磁盘或 FFmpeg 等必要条件异常 |
| `/healthz` | 外部端到端就绪 | 网关到后端链路或后端依赖异常 |

常用诊断命令：

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 frontend
docker compose exec backend ffmpeg -version
docker compose exec backend ffprobe -version
```

应用日志会过滤 Cookie、授权字段、CSRF、账号标识和签名媒体 URL。即使如此，诊断包和日志仍可能含视频标题、任务信息等个人数据，应只在受控渠道传输。

在线播放异常时按以下顺序检查：

1. 确认 `POST /api/v1/previews` 返回 201，响应中的 `manifestUrl` 为本应用 `/api/v1/previews/...` 路径。
2. 确认 MPD 返回 `application/dash+xml` 且不包含 `http://`、`https://` 或 Bilibili 签名查询参数。
3. 在浏览器网络面板确认媒体请求返回 206，并带有合法 `Accept-Ranges`、`Content-Range` 与 `Cache-Control: private, no-store`。
4. 若提示编码不支持，优先改选 H.264 + AAC；HEVC、AV1、HDR 的页面内播放取决于浏览器、操作系统和硬件，不影响下载原规格。
5. 若提示会话过期或媒体地址失效，关闭弹窗后重新播放；服务端会在同一规格地址失效时强制刷新一次。

预览会话只存在于单个后端进程内，默认空闲 30 分钟、绝对最多 6 小时、最多 32 个会话；单次 Range 最大 64 MiB，上游媒体请求并发最多 8。后端重启会结束正在播放的预览，这是预期行为，不会中断已交给任务系统的下载。不得在没有共享会话存储或会话路由设计时直接把 Uvicorn 横向扩为多个 Worker。

## 局域网与公网

标准 Compose 默认使用回环地址。移动设备需要在隔离且可信的家庭或办公局域网中临时验收时，可在 `.env` 中设置 `WEB_HOST=0.0.0.0`，然后执行：

```bash
docker compose up --detach --force-recreate frontend
```

同一网络中的设备可访问 `http://<主机局域网IP>:${WEB_PORT}`。该模式只发布 Nginx 网关，不发布 FastAPI 端口；未登录设备只能进入管理员登录页，业务 API 受会话与 CSRF 保护。由于 HTTP 仍不提供传输加密，管理员密码和会话可能被同网段攻击者窃听，因此不得在公共 Wi-Fi、访客网络、端口转发或公网 IP 上使用。验收结束后应把 `WEB_HOST` 改回 `127.0.0.1`。

长期局域网或公网部署应在本机网关之前增加受信任的 HTTPS 反向代理，并同时满足：

- 强制用户鉴权，凭据不写入前端构建产物。
- 使用有效 HTTPS，HTTP 自动跳转到 HTTPS。
- 限制允许的 Host、Origin、请求速率和上传大小。
- 对 SSE 禁用代理缓冲，将读取超时设置为长于任务连接周期。
- 保留 HTTP Range，并以流式方式代理大文件。
- 对 `/api/v1/previews/` 保留 `POST`、`GET`、`HEAD`、`DELETE`、206/416、`Range`、`Content-Range` 和长连接流式传输；不得缓存 MPD 或媒体响应，也不得把上游地址重写后暴露给客户端。
- 只把后端连接开放给受信任网关，不直接发布后端端口。
- 正确设置并限制可信代理地址，防止伪造客户端 IP 与协议头。

后端 `APP_NETWORK_MODE` 的安全语义：

- `local`：只允许回环 `APP_HOST`，适用于本机开发。
- `trusted_proxy`：只适用于后端没有外部端口、入口网关受控的部署。
- `public`：必须设置高强度 `APP_API_KEY`，否则配置校验拒绝启动；同时仍需 HTTPS、速率限制和租户隔离评估。

仅把 Docker 端口绑定从 `127.0.0.1` 改为 `0.0.0.0` 只适用于受控网络中的临时验收，不构成可长期运行的安全局域网部署。

## 资源与保留策略

下载与分析会消耗大量磁盘、CPU 和内存。部署前应为运行卷预留源视频、音频、中间文件和最终产物的总空间，并在设置页配置并发和自动清理周期。不得把 `/app/runtime/temp` 放在容量很小的容器可写层；标准 Compose 已将其放入持久运行卷。

容器使用非 root 用户、移除 Linux capabilities，并启用 `no-new-privileges`。Nginx 根文件系统只读，临时目录使用带容量限制的 tmpfs。

标准 Nginx 的 `/api/` 代理已关闭代理缓存、请求缓冲和响应缓冲，并保留一小时读写超时，可直接承载预览 Range、SSE 和产物下载。若替换入口网关，必须保持这些行为以及 `media-src 'self' blob:` 的 CSP，浏览器预览不需要直接放行任意 Bilibili CDN 域。
