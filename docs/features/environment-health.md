# 环境自检（`/api/health` 与 Platform Doctor）

## 一句话

启动后不用真的搜一次，就能知道「这台机器现在能不能用」：浏览器找不找得到、前后端版本是否一致、
每个平台能不能搜、为什么不能搜 —— 全部由本地状态推断，**不向任何平台发请求**。

## 代码入口

| 职责 | 位置 |
|---|---|
| 健康检查与降级判定（`/api/health`） | `api/services/environment_health.py`（`build_health_response`） |
| 浏览器探测（不启动浏览器，只解析路径） | `environment_health.py` 的 `_probe_browser`，底层用 `tools/browser_launcher.py` 的 `resolve_playwright_browser` |
| 每平台可搜性 / 诊断（Platform Doctor 的数据源） | `api/services/accounts.py`：`_platform_diagnostic`、`record_search_outcome`、`mark_login_required_from_search` |
| 响应模型 | `api/schemas/search.py`：`HealthResponse`、`HealthPlatformStatus` |
| 路由 | `api/main.py` 的 `GET /api/health` |
| 前端页面状态条 | `webui/src/components/search/PlatformStatus.tsx`、`webui/src/lib/environmentHealth.ts`、`webui/src/lib/statusDisplay.ts` |

## 关键决定

- **健康检查绝不访问平台**。它只做本地判断：浏览器可执行文件是否存在、
  读 `webui/package.json` 的版本号与 `API_VERSION` 是否一致、
  以及复用账号服务里已经算好的安全诊断。这样"打开页面"永远不产生平台流量。
- **浏览器探测结果缓存 30 秒**（`_BROWSER_CACHE_TTL_SECONDS`），且用 `asyncio.Lock` 防并发击穿 ——
  否则前端轮询会把 Playwright driver 反复拉起来。
- **诊断文案是"安全"的**：只暴露 `safe_code` / `safe_message` 与若干计数，
  绝不带 Cookie、请求行、平台原文（见 `accounts.py` 的脱敏处理）。
- 面向普通用户的界面不展示后端、Redis、Playwright 或版本构建术语；离线、版本不一致、浏览器缺失均给出可以直接照做的应用级提示。
- **`degraded` 的判定条件只有三个**：浏览器不可用、前后端版本不一致、
  或者"需要 Redis 但连不上"。其他情况一律 `ok` —— 不让健康检查因为无关原因变黄。
- **Redis 只在 `ENABLE_IP_PROXY` 为真时才检查**（`redis_required`）。代理池已于
  2026-09-14 整体删除，所以默认配置下这项恒为 `False`、`redis_available` 恒为 `None`。
  这块逻辑留着是为了不破坏既有响应结构。
- 搜索失败会反向降级账号状态（`mark_login_required_from_search`）：
  平台明确说"要登录"，卡片就直接变成需要登录，而不是等下次手动验证。

## 已知坑 / 边界

- **产品版本号已统一为三处一致，并有守卫**（2026-09-15）：
  `api/services/environment_health.py` 的 `API_VERSION`、`webui/package.json`、
  `pyproject.toml` 必须相同（此前 pyproject 写 `0.1.0`、另两处写 `1.0.0`，健康检查会因此误报 degraded）。
  守着这条的是 `tests/test_repo_hygiene.py` 的 `test_product_version_is_declared_consistently`，
  另有 `test_tagged_commit_declares_the_tagged_version` 在提交带 tag 时校验版本与 tag 一致。
  **发布版本的真正来源是 git tag**（`.github/workflows/release-package.yml` 从 tag 写
  `RELEASE_VERSION` 并带 `--verify-tag`），发版时把三处对齐到 tag 即可。
- `browser_extension/manifest.json` 的版本是**独立**的（走 Chrome 自己的更新渠道、独立发布节奏），
  刻意不与产品版本联动 —— 不要为了"整齐"把它改成跟产品一致。
- `_platform_statuses()` 是**按字符串 key** 从 `get_accounts()` 的 dict 里二次取值，
  没有强类型保证；字段改名只会在运行时退化成默认值，不会报错。
- `redis_required` 依赖 `getattr(config, "ENABLE_IP_PROXY", False)`（防御式写法），
  代理池删掉后这个开关实际没有任何消费方。
- 浏览器探测在**打包环境**下走的是 `playwright.chromium.executable_path`（会短暂启动 driver），
  首次调用比之后慢，属于预期。

## 测试怎么跑

```shell
.venv/Scripts/python.exe -m pytest -q --basetemp=.tmp_pytest_run tests/test_health.py tests/test_platform_doctor.py tests/test_browser_resolver.py
```

前端：`webui/tests/environmentHealth.test.ts`、`statusDisplay.test.ts`。
