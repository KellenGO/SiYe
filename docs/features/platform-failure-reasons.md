# 平台没搜到时的原因提示

## 一句话

某个平台这一轮**没搜到东西**时（0 条、失败、限流、超时、未登录），明确告诉用户"为什么"，
而不是只显示一句"无结果"：页面上方弹一次提示卡片，平台状态条上也常驻显示原因，
并在提示里说明该平台已被自动取消勾选。

## 代码入口

| 职责 | 位置 |
|---|---|
| 哪些状态算"没搜到"（含 0 条 `empty`） | `webui/src/components/search/SearchPage.tsx` 的 `FAILED_PLATFORM_STATUSES` |
| 顶部提示卡片（`position: "top-center"`，每轮每平台只弹一次） | 同上，`handledFailureKeys` + `toast(...)` |
| 原因文案优先级：平台自带的 `error_summary` → 通用文案 | 同上，`FAILURE_REASON_KEYS` |
| 状态条显示原因（`empty` 也算） | `webui/src/components/search/PlatformStatus.tsx` |
| 平台自报原因（抖音：0 条时区分"匿名/已登录"） | `aggregate_search/worker.py` 的 0 结果分支 |
| 把 worker 的 `error_summary` 透传到响应 | `api/services/search_job_manager.py`（`_read_worker_output` 的 status 分支） |
| 抖音搜索响应的安全诊断字段 | `media_platform/douyin/client.py`（`last_search_diag`） |

## 关键决定

- **0 条算搜索失败**（用户要求）：`empty` 与 failed / timed_out / rate_limited / login_required
  同等待遇 —— 弹提示 + **自动取消勾选并写进持久化偏好**（下次不白搜一遍）。
  代价是"关键词真的没内容"也会被取消勾选，用户已确认接受这一取舍。
- **原因优先用平台自报的 `error_summary`**，没有才回落到通用文案
  （`search.reasonEmpty` / `reasonLoginRequired` / `reasonFailed` / `reasonTimedOut` / `reasonRateLimited`）。
  `error_summary` 必须短、且是安全摘要（不含 cookie、URL、原始响应）。
- **抖音 0 条分两种情况**：
  - 匿名公开搜索（`pong` 未通过但开了 `allow_public_search`）→ `login_required`，提示去登录；
  - 已登录 → `empty` + `error_summary = "疑似平台风控"`。
  实测（2026-09-19，两个关键词）该平台的软风控签名是
  `status_code=0 / data 为空 / has_more=0 / logid 有 / 页面 xmst 有值` ——
  服务端正常应答但内容为空，换词与重新登录都无效。详见
  `docs/decisions/2026-09-19-单独获取某个平台.md`。
- **不做"连续空结果就冷却"**：`empty` 可能只是这个词没内容，加冷却会误伤正常搜索。

## 已知坑 / 边界

- 顶部提示的全局 `<Toaster>` **只能挂一个**（挂两个会把每条提示画两遍）；
  需要顶部居中的提示就在 `toast()` 上显式传 `position: "top-center"`。
- 提示的"每轮只弹一次"靠**模块级** `handledFailureKeys`（不是组件 ref）：切页再回来会重新挂载组件，
  ref 挡不住重复弹窗。
- 只有**确实勾着**的平台才会被自动取消勾选；补看一个没勾选的平台失败时，
  文案是「这次没有取到内容」而不是「已自动取消勾选」。
- worker 的 `error_summary` 以前在 manager 侧被丢掉（只读 `status`）—— 新增状态字段时
  记得同时改 `_read_worker_output` 的透传，否则前端永远看不到原因。
- 平台状态条 `<small>` 显示的是原因，完整内容同时进 `title`；`tests/test_webui_ui_contract.py`
  有契约盯着这两点。

## 测试怎么跑

- `pytest -q tests/test_webui_ui_contract.py`（0 条进失败集合、原因取值、i18n 键）
- `pytest -q tests/test_search_worker_supervisor.py`（假 worker 上报 `empty` + `error_summary`
  能到达响应；日志尾部输出）
- `pytest -q tests/test_douyin_search_diag.py`（抖音诊断字段只记形状、不记凭据值）
