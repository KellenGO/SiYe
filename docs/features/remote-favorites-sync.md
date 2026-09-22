# 跨平台收藏同步（拉取 + 持久化）

## 一句话

把各平台收藏归档到本机 SQLite。B站不设总条数上限，逐页保存、可中断恢复；**一键同步只拉新增**
（读到本机已有的内容段就停），另有一个不显眼的「完整重扫」用来彻底对齐。其他平台仍每次最多 100 条。

## 代码入口

| 职责 | 位置 |
|---|---|
| 持久化（表 `remote_favorites` / `remote_sync_runs`） | `api/services/remote_favorites_store.py` |
| 任务编排、逐平台落库、错误 drain | `api/services/favorites_job_manager.py`（`FavoritesJobManager`） |
| HTTP 路由（前缀 `/api/search`） | `api/routers/search.py`：`POST /favorites/jobs`、`GET /favorites/jobs/latest`、`GET /favorites/jobs/{job_id}`、`POST /favorites/jobs/{job_id}/cancel` |
| 请求 / 响应模型 | `api/schemas/favorites.py` |
| B站分页来源、增量停点、页大小与超时常量 | `aggregate_search/favorites_sync.py`（`BilibiliFavoritesSource`、`synchronize_favorites`、`PAGE_SIZE`、`INCREMENTAL_STOP_RUN`） |
| 账号隔离、检查点、基线比对、取全量结果 | `api/services/remote_sync_state.py`（`RemoteSyncStateMixin`） |
| 抓取侧（超时、分页、错误码） | `aggregate_search/worker.py` + 各平台 `media_platform/*/core.py` |
| 前端 | `webui/src/hooks/useFavorites.ts`、`webui/src/components/favorites/FavoritesPage.tsx`、`webui/src/components/search/ResultTabs.tsx`（`pageSize`） |
| 真实只读探测 / 隔离界面验收 | `scripts/probe_bilibili_favorites.py`、`scripts/remote_favorites_smoke.py` |

数据文件：与收藏库共用 `%LOCALAPPDATA%\SiYe\data\library.db`，不再跟随源码或发行包目录变化。

## 关键决定

- **B站逐页落库**：一页内容与检查点同事务保存；worker 在同一个稳定数据库落库后才报告进度，不通过进程消息累计整份收藏。其他平台继续逐平台保存，失败互不影响。
- 按 `(account_key, platform, content_id)` 去重合并（`ON CONFLICT DO UPDATE`）。
- **指标只合并、不倒退**：本次没取到的指标字段沿用本机已有的值，完整度只升不降（`api/services/favorite_snapshot.py`）。一次超时、被限流、或只走到列表阶段的同步，不能再把以前完整的指标覆盖成残缺版本；接口明确返回的 0 仍然是真值。
- **未取到的旧内容一律保留**：平台没有一致性快照，"这次没出现"不等于用户取消了收藏。同步只做"新出现的入库"，从不因为"这次没看到"删除任何东西，也不要求用户对缺失逐条裁决。
- 同一内容在多个远端收藏夹出现时保存多份归属，只归档一份内容。
- B站按真实 UID 隔离；历史上没带账号的同步结果留在 `default` 分组里，和普通收藏一样展示（页面不再有"账号未确认"这类需要用户理解的状态）。
- 旧 `default` 归档和已识别账号归档可能包含同一远端内容；两份原始记录都保留，但全量快照按 `(platform, content_id)` 合并后才交给页面。页面也会再做一次同身份防御性去重，避免旧响应或异常数据造成跨页签卡片残留。
- **一键同步 = 增量**（2026-09-22 调整，替换原"完整核对 + 缺失确认"）：逐收藏夹从第 1 页开始往后读，连续 **5 条**在该账号、该收藏夹的既有基线中出现，就停止当前收藏夹并继续下一个。连续计数可跨页，新增会清零；本轮刚保存的内容不能成为停点。
  - 为什么不是"碰到第一条旧的"：重新收藏、移动收藏夹、同一秒批量收藏都会让顺序抖动，一条旧内容出现在页首并不代表它后面没有新增。
  - 代价是**只看头部**：感知不到旧内容的深处删除/移动。这属于预期（未取到的旧内容保留），需要彻底对齐时用「完整重扫」。
  - 增量默认启用（`incremental_verified = True`，用户明确要求"不用每次重拉全部"）。**真实账号的排序稳定性仍未被证实**（见「已知坑」），这是接受的取舍。
- **「完整重扫」是兜底入口**：`sync_mode="full"` 从头翻到尾，并且只有它才清理旧的收藏夹归属（`finish_scan(reconcile=True)`）。平时不用点。
- 首次导入必须读到该收藏夹的末页，不能因断点附近已有内容提前停止；一个收藏夹完成后立即生成自己的基线，其他收藏夹的局部异常不影响它。中间短页以有效 `has_more` 推进，不因标称总数或不足 20 条报错；重复页、跨页重复内容及无法推进仍会停止该收藏夹并记录安全的页码诊断。
- 每页串行、请求起始至少间隔 2 秒；单请求超时 30 秒、worker 无进展 90 秒停止，不再给新 B站同步设 5 分钟总时限。平台受限、网络错误及服务端错误不自动重试。
- 新 B站同步不逐条补详情，列表已有信息直接保存；没拿到的互动数据沿用旧快照，不能据此承诺实时指标。
- **收藏页保持 V0.3 的形态**（2026-09-21 定）：平台勾选 + 一键同步 → 状态条 → 结果列表（平台页签直接点着切）。不做归档工作台、不做平台/状态/账号筛选器。
  - 结果一次拿全（`GET /favorites/jobs/latest` 不带 `summary`），但界面**一次只渲染 100 条**（`ResultTabs` 的 `pageSize`），底部「显示更多」逐次加 100；勾选与导出仍作用于筛选后的全部结果，不只是已渲染的那些。
  - 取全量结果走 `archive_page`，顺序 `ORDER BY platform, id DESC`（按平台分组、组内最近入库在前）。
- `archive_summary` 仍提供各类状态条数（`states`）与任务摘要，归档页已不再消费它们；`summary=true` 的省流快照供轮询使用。
- 相关取舍见 [B站收藏全量与缺失确认](../decisions/2026-09-21-B站收藏全量与缺失确认.md)。
- 启动时会把已知旧目录中的跨平台归档一起合并到稳定数据库；保留最早发现时间、最新内容快照、收藏夹名称和最新同步状态，旧库不删除。
- 打开页面**只显示上次保存的数据和同步时间，不自动访问平台**；只有用户主动点同步才更新。
- 遇限流 / 验证码 / 登录失效就停止该平台，保存已取内容并显示原因；**不自动重试、不降数量试探**。
- 取消 / 中断的同步落库为 `cancelled`，**不写 `running`**（否则前端会一直转圈）。
- 子进程 stderr 用并发任务**持续 drain**，避免日志塞满管道把进程卡死。
- 同步进行中或失败时仍展示本机旧内容；磁盘写入失败会单独提示，账号页的最近同步记录也显示失败，不能把“这次看到了”当成“已经保存”。
- 重启后仍保留互动数据的近似值标记，避免给旧快照制造虚假的精确度。
- 同步期间可点击「取消同步」：停止当前任务所有尚未完成的平台，等待保存已返回的内容后恢复同步入口。不会删除历史收藏，也不会撤销已完成平台的结果；取消请求失败时保留重试入口。
- 同步按钮同时显示加载圆圈与「正在同步 · 取消」，状态和取消操作始终可见；创建任务时显示「正在启动同步」，提交取消后显示「正在取消」。平台进度使用「同步中」。
- 平台状态同时显示「本次获取」和「本机保留」数量；未登录、限流、取消或只同步部分平台时，结果区仍返回并展示所有历史归档。

## 已知坑 / 边界

- 其他三平台和旧限量请求仍使用 `default` 归档，不宣传为多账号隔离；旧限量任务不能成为 B站全量同步基线。
- **增量的前提还没在真实账号上证实**：B站接口按 `order=mtime` 返回、预期是"最近收藏在前"，但 2026-09-21 那次真实只读探测返回 `LoginRequiredError`，没跑成。也就是说"读到连续 5 条旧的后面就都是旧的"目前**只是设计假设**。跑一次 `scripts/probe_bilibili_favorites.py`（只读，不写库）就能确认；如果哪天发现新内容没同步进来，先怀疑这里。
- 平台没有快照或变更日志保证。扫描会校验响应结构、重复内容、收藏夹前后变化及首部变化，但无法证明扫描期间完全没有深处变化。中间短页以有效 `has_more` 继续读取。**增量更是只看头部**：旧内容的删除、移动不会被发现（这是接受的代价，「完整重扫」用来兜底）。
- 当前覆盖自己创建的收藏夹及其接口可返回的条目，不包含订阅的他人收藏夹，不下载媒体。平台响应结构不符时停止，不冒充空收藏。
- 页面切换不中断后台任务，退出程序后需手动继续；现有搜索 / 账号操作互斥保留，长同步期间可先取消再搜索。
- 增量停点只优化日常"拉新增"，不能可靠发现深处删除、移动及旧内容修改（见上）。连续 5 条只是用户接受的轻量启发式，不是平台变更日志的替代品。
- **平台相关的东西不许写死**：分页大小与单请求超时是 `aggregate_search/favorites_sync.py` 的 `PAGE_SIZE` / `REQUEST_TIMEOUT`（落库换算位置也用它），平台名、页大小都从调用方传进 `begin_scan` / `save_sync_page`。复刻到其他平台时，这是前提条件之一。**上别的平台前必须先跑只读探测**（`scripts/probe_bilibili_favorites.py` 是模板），确认收藏时间是否单调、翻页是否稳定、总数是否一致——不稳的平台上指纹校验会频繁判「列表变化」，每次同步都白跑。
- 「可预期的同步失败」和「代码 bug」要分开：分页同步自己抛 `FavoritesSyncError`、归档落库抛 `SyncStateError`，两者才会被翻译成「列表变化，请重新同步」的安全文案；其他 `ValueError` 按原样暴露，别包装掉。

## 测试怎么跑

`tests/test_remote_favorites_store.py`、`tests/test_remote_favorites.py`、`tests/test_favorites_reliability.py`、`tests/test_library_migration.py`、`tests/test_favorite_snapshot.py`、`tests/test_bilibili_favorites_sync.py`（超过 100 条、多收藏夹、短中间页、断点恢复、局部异常、以及连续 5 条历史命中的停点）。其中覆盖四平台落库后两个平台未登录、重启后四个平台历史仍在。
浏览器侧：`scripts/remote_favorites_smoke.py`（打开页面只读本机缓存、平台页签切换、一次 100 条 + 显示更多、一键同步走 auto、完整重扫走 full、确认归档工作台已撤掉）。

`tests/test_bilibili_favorites_sync.py` 覆盖分页超过 100、恢复、分页漂移、账号隔离、逐收藏夹增量算法及 worker 协议。pytest 必须使用新的项目内 `--basetemp`。

前端运行 `npm run test:search` 与 `npm run build`，再运行 `scripts/remote_favorites_smoke.py`：临时数据库、隔离浏览器、拦截外部请求，验证平台页签、显示更多及 auto/full 两条路径。`scripts/probe_bilibili_favorites.py` 是显式真实只读探测，只取最多两页，不写归档；不作为常规测试运行。
