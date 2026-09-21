# 本地收藏夹

## 一句话

用户收藏保存在稳定的本机 SQLite 中；「已收藏」「放在哪些收藏夹」「稍后再看」是三个各自独立的状态，一条内容可以同时属于多个收藏夹。

## 代码入口

| 职责 | 位置 |
|---|---|
| 存储层（表 `items` / `collections` / `item_collections`） | `api/services/library_store.py`（`LibraryStore`，依赖注入 `get_library_store`） |
| 稳定数据目录与旧库自动合并 | `base/runtime_paths.py`（`library_data_root`）、`api/services/library_migration.py`（`migrate_legacy_libraries`） |
| 旧格式互动数据 / 元信息兼容编解码 | `api/services/favorite_snapshot.py` |
| 请求 / 响应模型 | `api/schemas/library.py` |
| HTTP 路由（前缀 `/api/library`） | `api/routers/library.py`，在 `api/main.py` 注册 |
| 前端 API 层（纯函数 + axios 两段） | `webui/src/lib/libraryApi.ts` |
| 前端状态与写操作 | `webui/src/hooks/useBookmarks.ts` |
| 收藏夹界面（左列表 / 右结果） | `webui/src/components/favorites/FavoritesPage.tsx` |
| 结果卡操作、收藏条目分组与归属编辑 | `webui/src/components/search/ResultTabs.tsx`、`webui/src/components/search/ResultTools.tsx`、`webui/src/index.css` |
| 一批条目的归属勾选三态（聚合卡片用） | `webui/src/lib/libraryApi.ts`（`collectionMembership`） |
| 备份导出 / 导入 | `webui/src/components/search/BookmarkBackup.tsx` |
| 屏幕正中的确认框（删除等破坏性操作） | `webui/src/components/ui/confirm-dialog.tsx` |

数据文件：Windows 默认为 `%LOCALAPPDATA%\SiYe\data\library.db`。测试和开发工具可用 `SIYE_DATA_DIR` 显式隔离；程序所在目录、源码目录或解压目录不再决定收藏库位置。

## 关键决定

- **一条内容可属于多个收藏夹，底层只存一份**：`UNIQUE(platform, content_id)` + 关联表。
- 固定内置视图按「全部 → 默认收藏夹 → 稍后再看」排列，再显示自建收藏夹。
- **「已收藏」是独立状态**（`saved`）：「全部」只显示已收藏的内容，且不可编辑——收藏了就一定在，没收藏就一定不在（也不会在任何其他收藏夹里）。只挂在稍后再看上的内容不进「全部」。
- **取消收藏 = 从「全部」消失**：清掉默认收藏夹与所有自建归属并置 `saved = 0`，不弹确认；**稍后再看不受影响**。取消收藏或取消稍后再看后两头都不沾的条目直接删除。
- **稍后再看完全独立**：只由卡片上的稍后再看按钮控制，不进归属面板，也不影响收藏状态。详见[收藏状态与稍后再看分离决策](../decisions/2026-09-21-收藏状态与稍后再看分离.md)。
- 结果卡右侧固定为「收藏 → 稍后再看 → 展开（有可展开内容时）」；前两个按钮只改变各自归属，不会连带修改另一个内置收藏夹或自建收藏夹。聚合卡片可按平台版本分别选择。
- 已收藏条目的「编辑归属」列出「全部」、默认收藏夹和自建收藏夹；「全部」保持勾选且不可编辑，其他归属可独立勾选。**面板里没有稍后再看**——它由独立按钮负责。
- 每条收藏的信息条最右侧是红色垃圾桶，点开屏幕正中的确认框即可彻底删除，不必进批量管理。搜索结果页则是刚收藏完弹出一次同样的信息条。
- **收藏成功后当场给一次「编辑归属」**：在该结果卡下浮现一行提示，点开就是同一套面板，不用去收藏页找条目。只在"加入"成功时触发，关闭后收起；聚合卡片按整批来源生效（勾选框半选表示部分命中）。详见[收藏后即时编辑归属决策](../decisions/2026-09-21-收藏后即时编辑归属.md)。
- 本地收藏以「内容主体 + 收藏信息」组成一个视觉组：保留两者之间的细线，用序号蓝色短线、信息区极淡底色和组间距明确归属；移动端隐藏序号后仍由底色和留白分组。归属卡中的长收藏夹名只做单行省略，悬停可查看完整名称，不能撑宽卡片。
- **删除收藏夹默认保留内容**，只解除归属；「取消收藏」是独立接口，避免误删。
- 批量管理支持加入或移出两个内置收藏夹、加入或移出自建收藏夹；只有「从本机彻底删除」会让条目从「全部」消失，并要求确认。
- 自建收藏夹不能新建或改名为系统名称。历史同名收藏夹原样保留，界面追加「（自建）」帮助区分。
- 重复收藏只刷新内容快照，**保留首次收藏时间与已有备注**；改备注只能走 `PATCH`。
- **互动指标只合并、不倒退**：重复保存时本次没带的字段沿用库里已有的值（搜索结果先出现、详情指标后补，补全前收藏一次不会把以后补上的值清掉）。
- 沿用既有前端限制：备注 1000 字、总量 500 条。
- 旧 `localStorage` 收藏**只提示、不自动删**；迁移有跳过项时**不写**完成标记（否则用户失去迁移入口）。
- 新版本首次启动会检查源码根、当前程序目录及旧 `dist/MediaCrawler`、`dist/SiYe` 的数据库。合并按平台与内容 ID 去重，保留非空备注和全部归属；迁移前备份目标库，旧库不删除。源库内容改变后可再次幂等合并。
- 迁移成功在收藏页显示一次恢复摘要；坏库会显示警告，不能读取的源不会清空现有目标库。
- 这次只移动 `library.db`；登录 profile、日志和其他缓存仍使用原位置。详见[稳定收藏库目录决策](../decisions/2026-09-16-收藏内置分类与稳定数据目录.md)。
- 并发重复收藏用 `INSERT OR IGNORE` + 冲突退化为更新（幂等），避免撞 UNIQUE 直接 500。
- 多个页面共享收藏状态；备注以实际写入成功为准，失败时保留草稿，方便直接重试。
- 导入结构出错时回滚整次操作；条目无效或超出容量则按实际跳过数量反馈，避免误报全部成功。

## 已知坑 / 边界

- `PATCH /items/{platform}/{content_id}` 把 content_id 放**路径参数**：含 `/` 的 ID 会被切段，
  可能改错条目或 404。当前数据无此情况，其他接口都走 body。
- 500 条上限在批量导入时按实际条目数判定，超限条目被跳过并反馈。
- 本地库**缺并发/锁测试**；长导入是单条 `BEGIN IMMEDIATE` 事务，并发写超过 10s 会超时。
- 传了已删除的 collection_id 会让该条内容**整条**收藏失败（单条 400，批量静默跳过）。
- 备注超 1000 字是**静默截断**，不给用户提示。
- `LOCALAPPDATA` 不可用时才回退到应用目录；目标目录不可写会让启动明确失败，不会静默改读空库。

## 测试怎么跑

- 后端：`tests/test_library_store.py`、`tests/test_library_api.py`、`tests/test_library_migration.py`、`tests/test_favorite_snapshot.py`
- 前端：`webui/tests/libraryApi.test.ts`、`webui/tests/resultLibrary.test.ts`
- 浏览器：`scripts/favorites_ui_smoke.py`（内置收藏夹、按钮顺序、稍后再看独立性、取消收藏后从「全部」消失、垃圾桶与居中确认框、归属面板不含稍后再看、长名称截断与桌面/移动分组布局）
- 跑法（`--basetemp`、node 绝对路径）见根目录 `AGENTS.md`。
