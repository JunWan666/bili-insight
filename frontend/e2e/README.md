# Playwright 端到端测试

本目录仅由 Playwright 测试进程加载，不会被 `src/main.ts`、Vite 应用入口或生产构建引用。

`fixtures/api.ts` 使用脱敏、固定且仅供测试的响应拦截 `/api/v1` 请求，确保 CI 不访问真实 Bilibili 服务。Cookie 上传用例中的文件由测试在内存中生成，其中域名为保留的无效域，值明确不是账号凭据；测试不会读取工作区中的 Cookie 文件，也不会在源码、报告或截图中放入真实凭据。

运行方式：

```bash
npm run test:e2e
```

首次运行前安装固定版本的浏览器运行时：

```bash
npx playwright install chromium webkit firefox
```

Linux CI 使用 `--with-deps` 分别安装每个矩阵任务需要的浏览器与系统依赖。可以按浏览器家族单独运行：

```bash
npm run test:e2e:chromium
npm run test:e2e:webkit
npm run test:e2e:firefox
npm run test:e2e:cross-browser
```

本机存在其他开发页面时，可通过 `PLAYWRIGHT_PORT` 选择一个独立端口；配置只接受 `1024`—`65535` 的整数。CI 默认使用 `4173` 且禁止复用已有服务。

## 自动化兼容矩阵

| 项目 | 引擎与用途 | 视口 | 输入能力 |
| --- | --- | --- | --- |
| `chromium-mobile-360x800` | Chromium，最窄手机门禁 | `360×800` | 触控、移动端 viewport |
| `chromium-mobile-390x844` | Chromium，常见手机门禁 | `390×844` | 触控、移动端 viewport |
| `chromium-tablet-768x1024` | Chromium，平板断点门禁 | `768×1024` | 触控 |
| `chromium-desktop-1440x900` | Chromium，桌面门禁 | `1440×900` | 鼠标/键盘 |
| `webkit-mobile-390x844` | Playwright WebKit，移动 Safari 兼容代理 | `390×844` | iPhone 用户代理、触控、移动端 viewport |
| `webkit-desktop-1440x900` | Playwright WebKit，桌面 Safari 兼容代理 | `1440×900` | 鼠标/键盘 |
| `firefox-desktop-1440x900` | Firefox 建议支持门禁 | `1440×900` | 鼠标/键盘 |

所有项目运行相同的核心流程与响应式断言；只在项目不具备对应设备能力时跳过触控专属用例。四个 PRD 指定尺寸始终由 Chromium 项目完整保留，WebKit 与 Firefox 是额外覆盖。

浏览器家族在 CI 矩阵中并行。WebKit 家族内部固定使用单 worker：在 Linux 无头环境中并发启动多个 WebKit 移动上下文会引入浏览器进程级页面重载抖动；单 worker、关闭重试的实测可稳定通过完整移动与桌面场景。Chromium 与 Firefox 继续使用 CI 默认的 2 workers。所有家族均不配置失败重试，避免把 flaky 用例误报为通过。

## 浏览器覆盖边界

- Playwright 的 `chromium` 验证 Chrome 与 Edge 共享的 Chromium 引擎、DOM、CSS 和通用交互行为，但不等同于分别在已安装的 Google Chrome、Microsoft Edge 或其企业策略环境中认证。浏览器渠道特有的下载策略、媒体组件和系统集成仍应在发布前按需做真机验收。
- Playwright WebKit 是 Safari 兼容性的高价值自动化代理，但不是 macOS Safari 或 iOS 真机 Safari，也不能证明 Apple 浏览器渠道特有的系统行为。对外宣称 Safari 兼容前，仍需在目标 macOS/iOS 与 PRD 要求的最近两个 Safari 大版本上完成发布验收。
- CI 使用 `package-lock.json` 固定的 Playwright 版本及其浏览器 revision，确保结果可复现；升级 Playwright 时应重新运行完整矩阵。Firefox 属建议支持项，但在 Linux CI 中与必需浏览器一样作为阻断门禁执行。

安装了 Google Chrome 与 Microsoft Edge 的发布机可执行 `npm run test:e2e:channels`，使用真实 `chrome` 与 `msedge` 渠道运行独立的桌面场景；该命令与 Playwright 自带 Chromium 门禁相互独立。
