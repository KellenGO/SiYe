# 登录与账号（浏览器扩展 / 内置扫码 / profile）

## 一句话

应用不保存账号密码，而是把使用者的登录态放进本机 `browser_data/<平台>_user_data_dir` 持久化 profile，
之后无头搜索复用。**应用内置扫码登录是主入口**，无需安装扩展；浏览器扩展同步已有登录状态是可选加速方式。

## 代码入口

| 职责 | 位置 |
|---|---|
| 浏览器扩展（MV3） | `browser_extension/`：`service_worker.js`（读 cookie → POST 后端）、`content_script.js`、`sync_protocol.js`（wire 契约，纯函数）、`popup.*` |
| 后端账号服务（一次性票据、域名白名单、Chrome→Playwright 映射、导入 profile、验证、删除） | `api/services/accounts.py` |
| 账号 / 登录 HTTP 路由（前缀 `/api/search`） | `api/routers/search.py`：`POST /login`、`GET /login/{job_id}`、`GET /accounts`、`POST /accounts/sync-ticket`、`POST /accounts/{platform}/sync`、`POST /accounts/{platform}/verify`、`DELETE /accounts/{platform}/session` |
| 扫码登录实现（可见窗口 + 二维码） | `aggregate_search/worker.py` 的 `_run_login`；二维码只走 `tools/crawler_util.py` 的 `show_qrcode`（`aggregate_search/login.py` 只是转发 shim） |
| 浏览器选择（自定义 > Chrome > Edge > 内置 Chromium） | `tools/browser_launcher.py`（`resolve_playwright_browser`） |
| profile 与登录态配置 | `config/base_config.py`：`USER_DATA_DIR`、`SAVE_LOGIN_STATE` |
| 前端账号页 | `webui/src/components/accounts/AccountsPage.tsx`、`useAccounts.ts`、`useAutoAccountSync.ts`；主结论与动作提示在 `webui/src/lib/accounts.ts` 的 `accountSearchVerdict` / `accountActionHint` |
| 扫码任务状态和跨页面搜索避让 | `webui/src/lib/scanLogin.ts`、`webui/src/App.tsx` |
| 前端扩展通信与批量同步 | `webui/src/lib/extensionSync.ts`、`accountBulkSync.ts`、`accountGate.ts` |

profile 目录：`browser_data/{xhs,dy,bili,zhihu}_user_data_dir`。

## 关键决定

- **扩展只是 cookie 搬运工**：从使用者日常浏览器读出 cookie，POST 给后端，
  后端导入应用自己的 profile（它不搜索、不抓取、不常驻）。
- 帮助页以扫码登录为主流程；扩展安装步骤放在默认收起的可选说明中。使用说明和网页指南也将扩展标为可选进阶路径。
- 扩展走**一次性同步票据**（128bit、60s、单次），后端只接受 `chrome-extension://` 来源。
- **扫码登录写入的 profile 就是搜索读取的那一个**，登录后 `_verify_login_success` 会真验证一次。
- 验证必须在浏览器会话关闭之前完成；此前会话关闭后验证导致“扫码成功但报失败”。已有登录时可直接验证成功，不必再扫一次。
- 可见浏览器自身展示二维码，不另弹系统图片查看器；无头模式仍保留图片二维码。
- 重启不信任磁盘上的旧验证结论。没有扩展时，自动复核本机已有登录状态；与扩展自动同步共用冷却，避免重复操作，全部成功时不打扰。复核过程和结果只保留右上角的紧凑状态提示，避免重复的大报告卡片遮挡首页。
- 切去搜索页仍保留登录轮询，任务结束后再释放搜索等待；不能因离开账号页就假定登录结束。整页刷新后仍由后端互斥兜底。
- 账号页分开展示登录验证与最近搜索／收藏同步结果，附实际观察时间；没有记录显示「尚未检测」，不把公开搜索成功当作登录证明。
- 进入账号页发起轻量检查，复用一分钟内的结果，最多同时检查两个平台；没有本机会话时跳过，繁忙或风控不自动重试。轮询只读本机状态，不向平台发请求。明确要求登录或开始新验证时立即废弃旧验证缓存，不能继续返回一分钟内的旧成功结论。
- 小红书接口响应缺字段归为无法确认；接口与浏览器不一致时，用同一 profile 的「我」入口复核一次。明确风控直接停止，不追加页面请求；临时验证失败保留现有会话快照。
- 扫码 worker 在浏览器关闭前完成验证，后端直接接受该成功结论；前端不再重开浏览器重复验证。新登录清除旧功能证据，旧任务不能覆盖新会话；缓存与取消不能作为使用成功证据。
- 历史功能结果只描述发生过什么，不保证此刻所有功能均可用。成功搜索可以撤销旧的笼统失效提示，但登录身份仍需实际验证。
- 为什么不能「应用直接读浏览器 cookie」：**Chrome 127+ 的 App-Bound Encryption**
  让外部程序即使拿到 cookie 数据库也解不开，只有跑在浏览器进程内的扩展能合法读取。

## 已知坑 / 边界

- 自动化验证使用模拟平台响应和浏览器交互；尚未使用真实账号复现用户的小红书状态，平台页面标记变化仍可能导致「暂未确认」。

- 扩展仍固定连接 8080；开发实例改端口时使用扫码登录。
- 扫码结果仍需四平台真实账号验收；自动化验证生命周期和界面交互，不能替代手机扫码。
- 登录态有效期（本机 profile 实测 cookie 标称）：抖音 `sessionid` ≈ 58 天、知乎 `z_c0` ≈ 178 天、
  小红书 `web_session` ≈ 332 天；实际使用会不断刷新，通常更久。
- worker 里 **CDP 模式被显式关掉**（`ENABLE_CDP_MODE=False`、`CDP_CONNECT_EXISTING=False`），
  所以「连到已打开的浏览器」这条路当前是关的（`tools/cdp_browser.py` 有实现可参考）。
- 换账号不会隔离：应用自己的 profile 是「一个平台一个目录」，同一台机器换账号会覆盖同一份 profile。

## 账号页：主结论「可用 / 不可用」与盲区（2026-09-18）

账号页卡片从「平铺所有状态」改成「动作卡片」：每张卡先一行回答
「这个平台现在能不能搜」，其余一律折叠进「详情」。

- **主结论**：`accountSearchVerdict` 给出 `可用 / 不可用 / 验证中` 三态与最短原因
  （如「登录已失效」「未登录」「浏览器不可用」）。判定只用前端能拿到的数据：
  账号状态（`GET /api/search/accounts`）+ 本机浏览器可用性（`GET /api/health`）。
  `accountActionHint` 回答「现在该点哪个按钮」。
- **详情折叠**：只向普通用户展示本机是否保存登录信息、验证时间、搜索／收藏结果、
  当前提示和建议。浏览器后端、路径、Cookie 计数、登录标记与错误码不在日常界面展示；
  排障所需的安全字段继续保留在帮助页主动生成的诊断报告中。
- **补上的盲区**：
  - 关卡 0 本机浏览器可用性——`/api/health` 的 `browser_available`，账号页顶部与
    每张卡主结论都会体现；浏览器不可用时四平台统一「不可用 · 浏览器不可用」。
  - 抖音允许公开搜索（`worker.py` 对 dy 开 `allow_public_search`）——静态产品规则，
    抖音卡主结论标「可用 · 抖音允许公开搜索」，并在详情说明"即使未登录也可能出结果"。
- **仍拿不到、本轮未做**：关卡 5 平台冷却（`cooldown_until`）只在搜索 job 响应里
  （`webui/src/types/search.ts`），账号页拿不到，没有编造；冷却状态仍在搜索结果页体现。
  如需在账号页展示，需后端在 `GET /api/search/accounts` 增加冷却字段。

## 测试怎么跑

- 后端：`tests/test_xhs_session_restore.py`、`tests/test_xhs_account_verification.py`、
  `tests/test_account_evidence.py`、`tests/test_session_snapshot_lifecycle.py`、`tests/test_extension_security.py`、
  `tests/test_extension_runtime.py`、`tests/test_account_coordinator.py`、`tests/test_account_sync_timings.py`
- 前端：`webui/tests/accountBulkSync.test.ts`、`accountGate.test.ts`、`accounts.test.ts`、
  `extensionSync.test.ts`、`useAccountsOptions.test.ts`
- 扫码回归：`tests/test_worker_login_done.py`、`tests/test_qrcode_popup.py`、`webui/tests/scanLogin.test.ts`；跨页轮询见 `scripts/favorites_ui_smoke.py`。
