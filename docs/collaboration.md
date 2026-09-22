# 协作记录

所有 agent 开工先读本页，再检查 Git 与相关功能 wiki；收工更新自己的记录。具体规则见 [AGENTS.md](../AGENTS.md)。本页记录任务与交接，不复述代码。历史实现以代码和提交为依据，功能说明见 [功能地图](index.md)。

## 当前任务

状态：只读 / 进行中 / 待核实 / 阻塞 / 待集成 / 完成。只有进行中的写入任务占用修改范围；暂停时写明未提交文件及是否仍需保留占用。已完成任务移入下方交接，避免当前表无限增长。

| 任务 ID | 负责人/会话 | 工作区 / 分支 / 起始 HEAD | 修改范围 | 状态与下一步 | 更新时间 |
|---|---|---|---|---|---|
| MIRROR-20260922 | Codex / 当前会话 | MediaCrawler-main / master / 48724e1 | B站、知乎远端收藏夹镜像闭环：SQLite/API、收藏页封面网格与分页浏览、同步诊断、测试与文档 | 待提交：卡片网格、单夹服务端分页/返回恢复、知乎类型身份迁移、目录保留和安全诊断已完成；后端+文档 94 项、前端 364 项和生产构建已通过。模拟浏览器验收完成，未访问真实平台。 | 2026-09-22 |

本表不表示所有外部会话都已登记。初始化时仅核实主工作区状态；其他工作区和会话仍需按开工流程核实。

## 最近交接

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
