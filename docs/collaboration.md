# 协作记录

所有 agent 开工先读本页，再检查 Git 与相关功能 wiki；收工更新自己的记录。具体规则见 [AGENTS.md](../AGENTS.md)。本页记录任务与交接，不复述代码。历史实现以代码和提交为依据，功能说明见 [功能地图](index.md)。

## 当前任务

状态：只读 / 进行中 / 待核实 / 阻塞 / 待集成 / 完成。只有进行中的写入任务占用修改范围；暂停时写明未提交文件及是否仍需保留占用。已完成任务移入下方交接，避免当前表无限增长。

| 任务 ID | 负责人/会话 | 工作区 / 分支 / 起始 HEAD | 修改范围 | 状态与下一步 | 更新时间 |
|---|---|---|---|---|---|
本表不表示所有外部会话都已登记。初始化时仅核实主工作区状态；其他工作区和会话仍需按开工流程核实。

## 最近交接

### V1-UX-COPY-20260923 — 收藏说明、热搜可用性与可选扩展步骤

- 负责人：Codex / 当前会话；主工作区 `MediaCrawler-main`，`master`，起始 HEAD `a32e19c`，起始状态干净。已核对当前任务表和相关近期交接，无重叠的进行中写入。
- 交付：跨平台收藏的可见说明改为只读取、不会改动平台收藏、可另存本地；小红书热搜标签在数据返回前就显示「暂不可用」，仍可点击看原因；帮助页原生折叠区默认收起可选扩展步骤，扫码登录在前。使用说明和网页指南把扩展移到快速开始之后，并同步热搜、收藏口径。修改范围限于对应界面、样式、文案、两份指南、三份功能 wiki、前端热搜用例、隔离浏览器烟测和更新记录；未动观看历史、登录、同步、抓取或热搜后端逻辑。
- 验证：`npm run test:search` 373/373，通过；`npm run build` 通过，`webui/dist` 已按本次源码重建；pytest `tests/test_webui_ui_contract.py`、`tests/test_docs_wiki.py`、`tests/test_trending.py` 76/76，通过（全新项目内 basetemp）；`scripts/getting_started_smoke.py` 在隔离 Edge 下通过，覆盖 390px 折叠/键盘操作、热搜标签/原因、其他平台模拟热词与无横向溢出；`git diff --check` 通过。烟测仅用临时库与模拟响应，没有访问真实账号、收藏或平台内容。
- 未做：未跑全量后端 pytest、未用真实平台账号验收；本任务不涉及这两项行为。源码与本地 `webui/dist` 一致，已打包 EXE 尚未重建。提交定位：`git log --all --grep=V1-UX-COPY-20260923`；已集成到主工作区 `master`，修改范围释放。

### V1-UI-FIXES-20260923 — V1 窄屏、按钮遮挡与长列表修复

- 负责人：Codex / 当前会话；隔离 worktree `codex/v1-ui-fixes`，起始提交 `668b28e`；功能提交 `4dc83de`，合入最新主线后的提交 `4eb872a`，已快进集成到 `master`。
- 交付：搜索、本机收藏、观看历史统一每批渲染 100 条；390px 收藏页标题与「列表 / 图标 / 备份管理」允许自然换行；窄屏普通通知改到底部，避免拦截右上操作；图标视图刷新后仍回到文件夹根页；320px 设置分类改为两列，不再裁掉最后入口。
- 边界验证：隔离 SQLite 写满 500 条本机收藏，首屏实际只渲染 100 张卡片，点一次「显示更多」后为 200，显示总数仍为 500。`ResultTabs` 的同一分批路径覆盖搜索和最多 1000 条的观看历史；筛选、导出与批量选择仍面向完整结果。
- 验证：最新主线合并后 `npm run test:search` 372 通过，pytest 文档与 UI 契约 63 通过，`npm run build` 通过；`scripts/favorites_ui_smoke.py` PASS，覆盖图标偏好刷新、390px 收藏页、320px 设置页、通知不遮按钮及 500 条上限。只使用临时库和模拟接口，没有访问真实账号、收藏或平台内容。
- 截图：`build/review-local-folder-grid-mobile.png`、`build/review-favorites-local-mobile-dark.png`、`build/review-settings-mobile-320.png`。源码已集成；主工作区正式前端构建会在本交接提交前再次生成。已打包的 EXE 不会自动更新，需要后续重新打包。

### ONBOARD-HIGHLIGHT-20260923 — 教程加「跟着目标走」的框选高亮 + 右下角常驻退出

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`534b359`。
- 缘起：用户提出教程需要强调的地方不够明显，希望在"让用户勾平台"时把全局变暗、给「搜索范围」加高亮框；
  并明确三点：**不要锁交互**（只给提示）、**框选位置要精确**（鼠标滚动、控件上移时跟着走，"像缩在一个图层里"）、
  **右下角要有随时能退出教程的入口**（避免用户不按教程交互后卡住）。先只读核对给出可行性分层，用户拍板后再实施。
- 修订既有结论：同日 `2026-09-23-教程扩为逐页导览.md` 曾把遮罩高亮作为 C 选项否掉
  （"把边看边操作变成被动点击，风险与收益不成比例"）。当时的判断把"铺遮罩"和"拦点击"当成一件事；
  这次拆开，只做压暗 + 框选 + 提示，**高亮层 `pointer-events: none`**，所以是缩小范围后的重开，不是推翻。
  新决策见 `docs/decisions/2026-09-23-教程高亮框选.md`。
- 范围：`webui/src/lib/onboarding.ts`（`GuideStep.highlight` + `guideHighlight`）、
  `webui/src/components/search/SearchBar.tsx`（`data-tour="search-scope"` 锚点）、
  `webui/src/components/help/GettingStarted.tsx`（卡片改为 `card` 变量 + 浮层放它外面）、
  新增 `webui/src/components/help/{GuideSpotlight,GuideExit}.tsx`、`webui/src/index.css`（`--siye-scrim` 深浅两套 +
  浮层样式 + 卡片抬到遮罩之上）、两个 locale 的 `common.json`（`homeHint` / `exit`）、
  `webui/tests/onboarding.test.ts`、`tests/test_webui_ui_contract.py`、`scripts/getting_started_smoke.py`、
  `docs/{index.md}`、`docs/features/getting-started.md`、`CHANGELOG.md`。未动后端、未动平台抓取、未动数据。
- 交付：① 「认识首页」这一步压暗页面其余部分、把「搜索范围」那一行框出来（比目标外扩 6px），
  旁边贴一句提示；位置按视口坐标实算并在 `scroll` / `resize` / 尺寸变化时重算。② 右下角常驻「退出教程」
  （语义等同「这次跳过」，只影响当前标签页），与 `.scroll-top` 上下错开。③ 压暗色 `--siye-scrim` 深浅各一值。
- 顺带修掉一个**静默失效的守卫**：`tests/test_webui_ui_contract.py` 原来用只认 `key` + `route` 的严格正则解析
  `GUIDE_STEPS`，步骤对象一旦新增字段，那一步就会从解析结果里消失 —— 守卫还在，但已经不管它了
  （实测：加 `highlight` 后旧正则只解析出 6 步）。正则已放宽，并新增两条守卫：带 `highlight` 的步骤必须有
  `<key>Hint` 文案、锚点 `data-tour="..."` 必须在组件里真实存在。
- 验证：`npm run test:search` **372** 通过（新增高亮声明用例）、`npm run build` 通过、`npm run lint` exit 0；
  pytest `tests/test_webui_ui_contract.py` + `tests/test_docs_wiki.py` **59** 通过（新增 1 条锚点/文案守卫）；
  `scripts/getting_started_smoke.py` PASS，新增断言：框与目标偏移精确为 `[-6,-6,12,12]`、滚动 220px 后仍精确、
  换深色主题与换 390px 视口后仍精确、**透过压暗层点得到平台勾选框**（证明没锁交互）、
  滚 600px 后退出入口仍在原位且可点、点退出后浮层全部消失且刷新仍关着。
  复核截图三张：`build/getting-started-review/{spotlight-light,spotlight-dark,spotlight-mobile}.png`。
  未访问真实平台与真实收藏。
- 截图复核发现并修掉一处：压暗层一开始把指令卡片也压暗了，里面可点的按钮看起来像禁用态 ——
  现在 `.has-onboarding .getting-started` 抬到遮罩之上。
- 交付定位：`git log --all --grep=ONBOARD-HIGHLIGHT-20260923`。`webui/dist` 是忽略产物，本地已重建、不进提交；
  已打包的 EXE 不会自动更新，需要重新打包。
- 未完成 / 待核实：① 未跑全量 pytest（只跑了文档与界面契约两个文件）。② 高亮只做了「认识首页」一处，
  其余六步是整页导览；再加一处只需在 `GUIDE_STEPS` 补 `highlight` + 目标补 `data-tour` + 两份 `Hint` 文案。
  ③ 窄屏下「收藏夹归属」会员弹层是 fixed 铺底的，退出入口层级比它高会浮在其上（刻意取舍）。
  ④ 与 `V1-UI-AUDIT-20260923` 的观测重叠仍待按新版本重跑。

### V1-UI-AUDIT-20260923 — V1.0 按钮、布局与数量边界审计

- 负责人：Codex / 当前会话；只读审计；工作区：`MediaCrawler-main`；起始提交：`810b03b`，结束时主分支已由另一会话前进到 `9ead7dc`。后者未修改本审计发现涉及的收藏页、历史页、全局提示或布局样式，结论仍适用。
- 已确认问题：① 390px/320px 下收藏页标题与「列表 / 图标 / 备份管理」并排互相挤压，按钮文字竖排；② 顶部右侧 toast 会覆盖这组按钮并拦截点击，收藏烟测两次因此超时；③ 图标模式虽写入本机偏好，刷新后 `localFolderRoot` 仍从 `false` 初始化，实际回到列表内容；④ 本机收藏 500 条一次渲染约 21,644 个 DOM 节点、2,024 个按钮，隔离浏览器首屏约 10.4 秒；历史 1000 条约 25,103 个节点、1,014 个按钮，约 2.7 秒；⑤ 320px 下设置页第三个导航按钮会被横向裁掉，需要不明显的横向滚动。
- 正常项：跨平台收藏的增量同步 / 重新同步全部 / 显示更多通过；收藏、备注、导出、复制、窄屏无页面级横向溢出通过；新手引导与诊断烟测通过。没有发现正常桌面宽度下按钮绑定到错误动作，也没有发现数量输入越过 1–40。
- 数据边界：全部检查使用模拟接口、临时数据和拦截网络，没有读取真实账号、收藏或平台内容。截图在 `build/review-local-folder-grid-mobile.png`、`build/ui-audit/favorites-local-320.png`、`build/ui-audit/settings-accounts-320.png`；临时审计脚本已删除。
- 本任务只报告、不修改产品代码。建议修复顺序：长列表分页/渐进渲染 → 移动端 toast 与标题操作区避让 → 图标偏好恢复 → 320px 设置导航可见性。

### PLATFORM-PREF-MEMORY-20260923 — 平台勾选严格按上次选择记住（含零勾选）

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`b9753d7`。
- 缘起：用户重申设计意图 ——「新手教程时四个平台都不勾、由用户自己勾，以后的勾选按上一次的记忆」。
  先做只读核对（隔离浏览器逐条读 `localStorage`）：首次进入 `[]`、勾小红书后刷新仍是小红书、
  首页↔搜索页往返不回退全选 —— 这三点在 `ONBOARD-PAGES-20260923` 之后已经成立；
  但**取消到零不落地**：存储留旧值，刷新后被取消的平台自己勾回来。
- 范围：`webui/src/lib/searchExperience.ts`（`platform_pref_set` 与两处注释）、
  `webui/tests/searchExperience.test.ts`（用例 ⑭）、`scripts/getting_started_smoke.py`（两条常驻断言）、
  `docs/features/{search-experience,getting-started}.md`、`docs/decisions/2026-09-23-平台偏好空数组语义.md`（追加「同日补充」）、
  `CHANGELOG.md`。未动后端、未动抓取、未动数据。
- 交付：偏好规则收敛成**「偏好 = 用户最后一次勾的样子，含一个都不勾」**。原来的"至少保留一个平台"
  不变量被删除 —— 它让存储与界面分叉（界面零勾选、存储留着旧值），并且在平台搜索失败被自动取消勾选
  之后会让这些平台在刷新时"复活"，与刚展示给用户的"已自动取消勾选"提示相反。
- 验证：`npm run test:search` 371 通过（用例 ⑭ 改为断言空集落地，并补"零偏好写回再读仍是零"）；
  `npm run build` 通过（dist 已更新）；`scripts/getting_started_smoke.py` 通过，新增"勾选跨刷新被记住"
  与"取消到零仍是零"两条断言。另有一条临时隔离脚本逐条打印各步的存储值（`.tmp_platform_pref_check.py`，
  已删除）。未跑全量 pytest。未访问真实平台与真实收藏。
- 交付定位：`git log --all --grep=PLATFORM-PREF-MEMORY-20260923`。`webui/dist` 是忽略产物
  （`webui/.gitignore`），本地已重建，不进提交；已打包的 EXE 不会自动更新，需要重新打包。
- 待核实：① 旧的"至少留一个平台"规则是 `ONBOARD-PAGES-20260923` 之前某次会话加的（测试里带注释说明用意），
  本次按用户口径推翻；若当时另有原因，需要在本条下面补记。② 未跑全量 pytest。

### ONBOARD-PAGES-20260923 — 新手教程扩为七步逐页导览
- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`810b03b`。
- 范围：`webui/src/lib/{onboarding,searchExperience}.ts`、`webui/src/hooks/useOnboarding.ts`、`webui/src/components/help/{GettingStarted,HelpPage}.tsx`、两个 locale 的 `common.json`、`webui/tests/{onboarding,searchExperience}.test.ts`、`tests/test_webui_ui_contract.py`、`scripts/getting_started_smoke.py`、`docs/{index.md,使用说明.md}`、`docs/features/{getting-started,search-experience}.md`、新增两条 decisions、`CHANGELOG.md`、`site/guide.html`。未动后端、未动平台抓取、未动数据。
- 交付：① 教程从四步改成七步「一步一页」（连接平台 → 首页 → 搜索与结果 → 收藏与整理 → 观看历史 → 外观与个性化 → 帮助与反馈），每步文案升级为"这一页是什么 + 能做什么"；步骤来源收敛成 `GUIDE_STEPS`（key + route），步数校验按长度动态。② 帮助页新增「页面与功能一览」（10 条逐页速查），教程页脚指向它。③ 修掉搜索页平台勾选「往返一次就变全选」：`parsePlatformPref` 对显式空数组返回空集，零勾选在刷新/切页后保持。取舍见 `docs/decisions/2026-09-23-教程扩为逐页导览.md`、`docs/decisions/2026-09-23-平台偏好空数组语义.md`。
- 为什么②③与①同船：教程文案承诺「平台默认一个都不勾，由你亲手勾」，不同期修掉那个往返副作用就是发布了假承诺；两处行为改动落在互不相干的文件上。
- 验证：前端 `npm run test:search` 371 通过（新增 4 条：步骤数边界、步骤路由、空偏好往返、空数组语义）、`npm run build` 通过；pytest `tests/test_docs_wiki.py` + `tests/test_webui_ui_contract.py` 58 通过（新增 2 条守卫：每步中英文案齐全、帮助页速查与教程互相指得到）；`scripts/getting_started_smoke.py` 通过——隔离 SQLite + TestClient + `webui/dist` + Playwright 拦 `/api`，覆盖许可前隐藏、七步全程与每步 URL、平台零勾选、回搜索结果、跳过、重开、刷新恢复、390px 窄屏 + 深色主题、诊断复制/下载/离线。未访问真实平台与真实收藏。截图在 `build/getting-started-review/`（构建产物，未提交）。
- 交付定位：`git log --all --grep=ONBOARD-PAGES-20260923`。源码与本轮 `webui/dist` 生产构建一致；已打包的 EXE 不会自动更新，需要重新打包。
- 未完成 / 待核实：① 未跑全量 pytest（只跑了文档与界面契约两个文件；平台偏好改动同时被前端 371 条用例覆盖）。② 教程的第 7 步只是把用户带到帮助页，没有做页内锚点高亮（help page 已有 `id="pages"`，将来要跳转可直接用）。③ `docs/使用说明.md` 与 `site/guide.html` 的正文已同步为"七步"；`site/index.html` 复核后确认它只写「应用内提供可跳过的新手引导」、不含步数，无需改。④ `docs/plans/2026-09-18-更新方向与难度评估.md` 的第 1 条已加「已实施（2026-09-23）」状态说明，避免 `GUIDE_ROUTES` / `^[0-3]$` 这两个已不存在的符号继续被当成现状。⑤ 与本表里 Codex 的只读 UI 审计（`V1-UI-AUDIT-20260923`）有观测重叠：它审的是改动前的构建，结论需要按新版本重跑。

### V1-PUBLIC-COPY-20260923 — V1.0 面向普通用户的界面文案

- 负责人：Codex / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`5c39887`；开发版快照标签：`developer-snapshot-pre-v1.0`。
- 交付：收藏页移除接口、分页、镜像、数据库等实现说明，将「完整重扫」改为「重新同步全部」；账号、启动检查、扫码登录和平台错误只呈现用户能理解的状态与下一步，不再展示本地 API、浏览器后端、Cookie 数量、内部标记、错误码或 Playwright 命令。帮助页保留可选浏览器扩展的必要安装步骤，以及不会自动上传的脱敏诊断入口。产品版本已统一为 `1.0.0`。
- 行为边界：未改变搜索、同步、收藏、账号验证或平台读取逻辑；原始诊断仍保留在日志或用户主动生成的脱敏报告里。新增界面契约守卫，防止被移除的开发者措辞重新出现在用户界面。
- 验证：前端 `npm run test:search` 367/367、`npm run build` 通过；相关后端、文档和界面契约 pytest 181 通过（2 跳过），最终增量复核 56 通过；三个隔离 Playwright 烟测通过，使用临时 SQLite 和模拟账号/API，没有访问真实平台、账号或收藏。截图：`build/review-v1-remote-favorites.png`、`build/review-account-wording.png`、`build/getting-started-review/help-v1.png`。
- 交付定位：`git log --all --grep=V1-PUBLIC-COPY-20260923`。源码与 `webui/dist` 的生产构建一致；已打包的 EXE 不会自动更新，需要后续重新打包。

### REMOVE-UNCLASSIFIED-20260923 — 本地收藏去掉「未分类」视图

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`d36e12e`。
- 范围：`webui/src/components/favorites/FavoritesPage.tsx`、`CHANGELOG.md`、`docs/features/favorites-library.md`、`docs/decisions/2026-09-23-移除未分类视图.md`。只删界面入口，后端（`api/routers/library.py`、`api/services/library_store.py`）与 `webui/src/lib/libraryApi.ts` 的响应类型未动。
- 结果：列表侧栏与图标网格都不再有「未分类」；`LibrarySelection` 的 `unclassified` 分支、`unclassifiedCount` memo、`localFolderCards.unclassified` 与那条摘要文案一并删除（不留死代码）。后端 `stats.unclassified` 与 `GET /items?unclassified=true` 保留为数据能力，取舍与理由见新加的决策记录。内容不会因此失去入口——「全部」的口径是 `saved`，与归夹无关。
- 验证：前端 `npm run test:search` 367 通过、`npm run build` 通过；pytest `tests/test_docs_wiki.py` + `tests/test_webui_ui_contract.py` 通过。隔离渲染（临时 SQLite + TestClient + `webui/dist` + Playwright 拦 `/api`）在列表模式与图标模式两种视图下确认「未分类」不再出现、其余内置视图与自建夹不受影响。后端字段仍在，`tests/test_library_store.py` / `tests/test_library_api.py` 未改、未跑（本轮不动后端）。
- 交付定位：`git log --all --grep=REMOVE-UNCLASSIFIED-20260923`。`webui/dist` 随本轮生产构建更新；已打包的 EXE 不会自动更新。
- 未完成 / 待核实：`webui/src/lib/libraryApi.ts` 的 `stats.unclassified` 字段现在只剩类型声明（前端无消费方）——保留是刻意的，理由见决策记录。

### FOLDER-CARD-BILI-20260922 — 收藏夹卡片对齐 B站 的「一页海报 + 两层副本」

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`88a7185`。
- 范围：`webui/src/index.css`、`webui/src/components/favorites/FavoritesPage.tsx`、`tests/test_webui_ui_contract.py`、`docs/features/remote-favorites-sync.md`、`docs/features/favorites-library.md`。跨平台与本机图标模式两套卡片一起改；未动后端、未动平台抓取、未动数据。
- 交付：卡片改成「一页 16:9 海报 + 后面两层副本」——副本层与海报同宽同高，每退一层左右各内缩 8px、上移 5px，只从顶部露出一条阶梯；海报圆角 10、无描边；数量从下面那行挪到海报右下角的白色角标（底部加自下而上的暗渐变保证可读），那行只留「平台 · 只读」。顺带修三个真 bug：① 五个从未定义的 `--siye-*` 变量（`--siye-surface` / `--siye-border` / `--siye-primary` / `--siye-text` / `--siye-danger`）——其中 `border` 简写因变量无效整条降级成 `border-style: none`，**两层副本一个像素都没画出来**，这也是用户说的"没有多个文件的感觉"的直接原因；② `.remote-folder-cover` 缺 `grid-area: 1 / 1`，心形兜底图标独占一行网格、封面被压成 2.3:1（169px 宽只剩 85px 高）；③ 副本层方向写反（`translate(+7px, +8px)` 往右下摊，会压到夹名）。
- 验证：`npm run test:search` 367 通过、`npm run build` 通过、`pytest tests/test_docs_wiki.py tests/test_webui_ui_contract.py` 54 通过（含新增 2 条守卫：CSS 幽灵变量、两套卡片几何一致）。真实渲染校验用一次性隔离脚本（临时 SQLite + TestClient + `webui/dist` + Playwright 拦 `/api`，**未提交**）：11 个构造收藏夹下卡片等宽 165px、海报 16:9=1.778、副本层内缩 8/16px 与上移 5/10px 误差 <1.5px、副本层底色非透明且深浅不同、角标白字落右下、无封面夹仍 16:9 且有底色、深色主题下副本层仍有底色、无 pageerror。截图 `build/review-folder-cards-bili.png`（整段）、`build/review-card-single.png`、`build/review-card-fallback.png`、`build/review-folder-card-vs-bili.png`（与用户给的两张截并排），`build/` 已被 gitignore。
- 数据边界：只改视觉与一处 JSX 角标节点，未改接口、未改库、未访问平台或真实收藏库；角标文案沿用全站既有的「条」。
- 未完成 / 待核实：未在真实 B站 数据上跑（本轮不访问账号）；未跑全量 pytest（只跑了文档与契约两个文件）。另发现一处**未改动**：图标模式的浏览偏好（`siye.favorites.local-folder-view.v1`）重新打开页面后不会直接进图标网格——`localFolderRoot` 初始为 `false`，得再点一次工具栏的「图标」；本轮只做卡片视觉，没碰这个状态初始化。
- 交付定位：`git log --all --grep=FOLDER-CARD-BILI-20260922`。`webui/dist` 已由本轮生产构建对齐；用户手上已打包的 EXE 不会自动更新。
- **追加（用户看完实机后的返工）**：去掉封面格里的占位图标（`FolderHeart` / 分类 `<Icon />`）。根因是层叠上下文：图标带 `opacity: .5`，会被排到比流内图片更晚的绘制层，DOM 顺序（图在后）压不住它，于是每张封面正中都浮着一个心形。处理：① 两套卡片的封面格不再渲染任何图标（空夹只剩浅色渐变底 + 数量角标）；② 图片保留 `position: relative; z-index: 1` 作为防线，并在 index.css 注明"日后若要加占位图标必须靠它压住"；③ 契约守卫加两条断言——图片必须带 `z-index: 1`、封面格 markup 里不许出现 `FolderHeart` / `<Icon />`。返工后重跑：`npm run test:search` 367、`npm run build`、pytest 两文件 54 全过；隔离渲染 22 项几何/渲染断言全过（新增"封面格无 svg"与"封面中心 `elementFromPoint` 命中 IMG"两项）。截图已重生成。

### LOCAL-FOLDER-GRID-20260922 — 本地收藏夹图标模式

- 负责人：Codex / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`5eb6972`。
- 交付：列表模式保持默认；工具栏新增可记忆的「列表 / 图标」。图标根页将全部收藏、未分类、内置分类和自建夹分开显示为封面卡片；进入后继续使用原有可编辑纵向内容列表，返回恢复网格位置。自建夹封面按已有归属的加入时间，内置分类按本机收藏时间降级；不使用内容发布时间，也不访问平台。
- 数据边界：仅透出已有 `item_collections.added_at` 供前端排序，未迁移或重做收藏模型；本地夹仍可新建、改名、删除和编辑归属，远端镜像未改。
- 验证：后端收藏库/API 44 通过；前端 `npm run test:search` 367 通过、`npm run build` 通过；文档守卫 52 通过；隔离 SQLite + Playwright 烟测覆盖有封面/无封面、空夹、未分类、多夹、进入/返回、390px 无横向溢出与本机偏好。截图在 `build/review-local-folder-grid.png`、`build/review-local-folder-detail.png`、`build/review-local-folder-grid-mobile.png`（构建临时产物，未提交）。未访问真实平台或真实收藏库。
- 交付定位：`git log --all --grep=LOCAL-FOLDER-GRID-20260922`；源码与本轮 `webui/dist` 生产构建一致，已打包 EXE 不会自动更新。

### MIRROR-20260922 — B站、知乎远端收藏夹只读镜像

- 负责人：Codex / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`48724e1`。
- 交付：`0418099 feat(favorites): complete remote folder mirror MIRROR-20260922`（已在主分支，本地提交，未推送）。跨平台收藏页新增只读封面卡片网格，卡片摘要批量读取；可进入单夹服务端分页、显示更多并返回原卡片滚动位置。B站、知乎目录/内容身份与镜像可用；小红书仍只显示单一收藏笔记入口，抖音未启用镜像。
- 数据边界：知乎远端内容身份改为类型加数字 ID；完整目录中消失的夹标记平台暂未找到但不清空镜像。同步数量文案是本次读取并保存，不是新增量；逐夹安全诊断持久记录模式、基线、页数、返回数与停止原因。
- 验证：后端与文档守卫 94 通过、前端 `test:search` 364 通过、`npm run build` 通过。隔离数据库 + 8127 本地源码服务的浏览器验收覆盖卡片网格、空夹、进入夹、50+5 分页、窄屏；没有访问真实平台。源码与 `webui/dist` 已由本轮生产构建对齐；用户实际运行的已打包 EXE 不会自动更新。
- 后续：真实 B站/知乎只读排序、封面与目录形状仍待用户手动同步后观察；小红书真实多夹和抖音账号/目录能力明确未验证。

### LONG-LIST-NAV-20260922 — 底部「显示更多」靠右 + 新增返回顶部

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`dbd3e23`。
- 范围：`webui/src/{index.css,App.tsx}`、`webui/src/components/search/ResultTabs.tsx`、新增 `webui/src/components/layout/ScrollToTopButton.tsx`、两个 locale 的 `common.json`、`tests/test_webui_ui_contract.py`、`docs/index.md` + 新增 `docs/features/long-list-navigation.md`。
- 结果：① 修 `.library-batch-bar` —— 它此前只有类名没有样式，文字与「显示更多」挤在一起、按钮被压扁；现在 flex + `space-between`（文字左、按钮贴右）、按钮走 `.btn small`、顶部一条发丝线收口。② 新增返回顶部按钮，挂在应用根部（`App.tsx`），滚过 400px 淡入、固定视口右下角、走 `action.backToTop` i18n 键，宽窄屏都不与 `.membership-card` 抢右下角。
- 验证：前端 `npm run test:search`（364 通过）与 `npm run build`（tsc -b + vite build）通过；`tests/test_docs_wiki.py` + `tests/test_webui_ui_contract.py` 共 52 通过（含新增 3 条契约断言）。真实渲染校验用一次性脚本（隔离 SQLite + TestClient + `webui/dist` 构建产物 + Playwright）：跨平台收藏页 120 条时 `.library-batch-bar`「已显示 100 / 120 条」按钮右边界与容器右边界相差 <1px、高 32px；`.scroll-top` 未滚动时 `visibility: hidden`、滚到底 `is-visible`、`position: fixed`、点击后 `scrollY < 5`；390px 视口无横向溢出。**该脚本是一次性的，未提交**。
- 交付定位：`git log --all --grep=LONG-LIST-NAV-20260922`。
- 未完成 / 待核实：`scripts/favorites_ui_smoke.py` **目前已过时**（既有问题，非本次引入）：它在跨平台收藏流程找 `重新同步` 按钮，而当前界面已是「同步收藏 / 同步所选平台」等文案（`FavoritesPage.tsx` 与 2026-09-21 的 `2968f7e`「收藏页回到 V0.3 形态；一键同步改为增量」相关），脚本在第 256 行即中断。该段属于 MIRROR-20260922 占用的范围，本次**未擅自修改**；修好后可把长列表断言补进脚本尾部。
- 另一处观察（有意保留，未改）：**本机收藏页不传 `pageSize`**，一次渲染全部收藏，因此「已显示 / 显示更多」只出现在跨平台收藏页与搜索结果页；用户界面里若看到本机收藏页有这条，那是旧构建。详见 `docs/features/long-list-navigation.md`。

### DOC-SHOTS-20260922 — README 产品截图更新（含打码）

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`6d3cc5f`。
- 范围：`README.md`（重写「产品截图」章节）、`docs/images/`（新增 8 张截图）。未触碰 MIRROR-20260922 占用的收藏相关代码。
- 结果：README 改为嵌入 `docs/images/shot-*.png` 共 8 张（首页 / 搜索 / 本机收藏 / 跨平台收藏 / 历史 / 账号与登录 / 外观浅色与暗色），不再引用 `site/shot-*.png`。
- 隐私处理：截图里的内容封面缩略图做了像素化、作者昵称用灰色圆角条覆盖；未打码原图只存在于会话临时目录，未进仓库。
- 交付定位：`git log --all --grep=DOC-SHOTS-20260922`。文档与图片交付，不涉及应用构建或部署。
- 验证：人工核对打码后截图；`git status` 确认仅上述文件。未运行测试（无代码改动）。
- **后续（同任务追加，2026-09-22）**：落地页同步换图 —— `site/` 8 张截图全部换为打码版（shot-search 裁掉底部半条，1600×700；其余 1600×773）；`site/index.html` 首屏图换新并在 features 后新增「界面一览」区块（`.shots` 网格 + 6 张卡，含浅/深主题并排卡）；新增 CSS 已同步进 `guide.html`；guide 两处 `figure.shot` 与 `docs/使用说明.md` 图注随新图更新（账号图从「4/4 已登录」改为 2/4 实拍状态）；`og.png` 已用 `scripts/build_landing_og.py` 重新生成；`site/维护说明.md` 截图表与流程已同步。Playwright 冒烟截图确认 `#shots` 区块浅深主题与 guide 图渲染正常。
- **后续（同任务追加之二，2026-09-22）**：修落地页 `#mobile` 区块布局 —— 两张卡片在 ≥980px 的通用三列网格里只占 2/3 宽、右侧留白，改为 `#mobile .cards` 两列铺满；按钮行 `.cta-row` 原本只有 `margin-bottom`、紧贴卡片，补 `margin-top:22px`（`.small-note` 一并到 18px）。规则加在 `.cards` 声明后，已同步进 `guide.html`（该页无 `#mobile`，仅为两份 CSS 保持一致）。实测（视口 1440）：`#mobile .cards` 与 `.cta-row` 都是 180→1260，与 `--max` 内容宽对齐，按钮上间距 22px；900px 视口下两列正常。
- **后续（同任务追加之三，2026-09-22）**：应要求把 `index.html` 的区块底色统一为白 —— 去掉 `#features`、`#mobile`、`#faq` 三处 `class="tint"`（此前 `#mobile` 与 `#faq` 相邻两段灰底尤其扎眼）。`section.tint` 规则保留未删，想恢复某段灰底加回 class 即可；`site/维护说明.md`「改文案 / 改配色」已记这条约定。实测所有 section 背景计算值均为 `rgba(0,0,0,0)`（继承纯白 `#fff`），卡片与 FAQ 靠边框在白底上仍可分辨。
- **后续（同任务追加之四，2026-09-22）**：把两个页面的标签页图标换成四野放大镜图标（源自 `assets/siye-icon.png`，裁掉透明边距 → 96×96 PNG → base64 内联），替掉原来那个「淡紫圆角块 + 渐变条」的占位 favicon。保持内联是为了守住「零外部请求、单文件自包含」的既有约定：实测两页 `link[rel=icon]` 为 `data:image/png`（10438 字符），页面请求非本地数为 0。`site/维护说明.md`「改文案 / 改配色」已更新（原来说网站图标与字标渐变同色、三处一起改，现已不适用；并注明 `webui/public/favicon.svg` 是无人引用的红圆残留）。

### MOBILE-CREDIT-20260922 — Android 客户端归属署名改为 MMY

- 负责人：WorkBuddy / 当前会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`1b1fbe6`。
- 范围：`README.md`（顶部说明与「移动端（Android）」）、`site/index.html`（移动端板块与 FAQ 一条）共 4 处文案：把「由朋友独立开发 / 维护」改为「由 MMY 独立开发 / 维护」。
- 不动：`docs/decisions/2026-09-15-产品命名与仓库名.md` 里提到「朋友的 Android 版」属当时的决策记录，保留原文；仓库链接 `metaMMY07/MediaCrawler` 未改。
- 交付定位：`git log --all --grep=MOBILE-CREDIT-20260922`。纯文案交付，未运行测试。

### COORD-20260922 — 统一协作规则和交接入口

- 负责人：Codex / 当前协作规则会话；工作区：`MediaCrawler-main`；分支：`master`；起始提交：`ab89a90`。
- 范围：`AGENTS.md`、`docs/collaboration.md`、`docs/index.md`，及本机流水账。
- 结果：建立开工检查、任务登记、单工作区单写入负责人、跨 worktree 可见性说明和收工交接规则。
- 交付定位：`git log --all --grep=COORD-20260922`。随该提交生效；文档交付，不涉及应用构建或部署。
- 验证：现有文档守卫 9 项通过，`git diff --check` 通过；`git worktree list` 当前仅登记主工作区。未改产品代码，不运行前后端业务测试。
- 后续：执行 agent 必须遵守入口规则；旧会话不会被文档自动唤醒。没有新增自动锁或消息通知系统。

### 初始化基线（从提交与当前代码检查得到，不替代原作者验收）

- `0a9c951`：已添加多平台 token 分页适配。当前 B站/知乎具有收藏夹来源，小红书仍是单一收藏笔记列表，抖音新适配器身份识别仍为阻断占位。真实平台能力不能仅凭提交标题判为全部完成。
- `312ae42`、`ab89a90`：登录复核报告居中及状态提示精简，已在当前主分支；本次未重新做浏览器验收。
- 跨平台收藏只读收藏夹镜像：本会话提出过方案，尚未在本任务实现。

## 新任务记录模板

开工在当前任务表登记，收工移到最近交接：

- 任务 ID / 负责人或会话标识 / 更新时间：
- 工作区 / 分支 / 起始 HEAD / 修改范围：
- 状态 / 已做事项 / 相关 wiki 或决策：
- 交付提交（或包含任务 ID 的 Git 查询）/ 是否已集成：
- 验证通过、失败、未运行与原因：
- 未完成事项 / 下一步 / 未提交文件归属：
- 运行版本说明（涉及界面或服务时）：

只写对下一位接手者有用的信息，不记录凭据、平台原始响应、用户收藏内容或整段终端输出。记录较多时，旧的完成交接可移到按月的 `docs/history/` 文件，并在本页保留链接；当前任务与仍有待办的交接不归档。
