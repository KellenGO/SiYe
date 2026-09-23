# 长列表：显示更多与返回顶部

## 一句话

列表长到几百条时，一次只渲染前 100 条、其余点「显示更多」逐步展开；搜索结果、观看历史和收藏列表向下滚动后，右下角出现「返回顶部」，一键回到顶部。

## 代码入口

| 职责 | 位置 |
|---|---|
| 「已显示 x / y 条 + 显示更多」与分页渲染 | `webui/src/components/search/ResultTabs.tsx`（`pageSize` prop、`shown` 状态） |
| 底部条的样式（`.library-batch-bar`）与返回顶部样式（`.scroll-top`） | `webui/src/index.css` |
| 返回顶部组件 | `webui/src/components/layout/ScrollToTopButton.tsx` |
| 搜索与历史的隔离浏览器验收 | `scripts/scroll_to_top_smoke.py` |
| 挂载点（所有页面共用，挂在应用根部） | `webui/src/App.tsx` |
| 按钮无障碍名称文案 | `webui/src/i18n/locales/{zh-CN,en-US}/common.json`（`action.backToTop`） |

## 关键决定

- **返回顶部挂在应用根部**，不是在某个页面里：收藏、历史、结果页都可能很长，挂一次处处可用；短页面（首页、设置）因为滚动不足阈值而看不到它，不用逐页判断。
- **固定在视口右下角**（`right/bottom: 24px`），这是主流习惯，也避开内容区——内容本身居中，右侧留白正好放它。
- **收藏等页面滚过 400px、搜索结果和历史页滚过 160px 才淡入**：搜索与历史列表较短时也容易找到入口，停在页首时仍不干扰阅读；按钮用 `visibility` 隐藏而不是只透明，隐藏时不可点、不进 Tab 序列。
- 点击回顶时读 `prefers-reduced-motion`：系统要求减少动态效果就瞬时跳转（`behavior: 'auto'`），否则平滑滚动。
- **搜索结果、本机收藏、跨平台收藏和观看历史统一每批渲染 100 条**：筛选、导出和批量选择仍面向完整结果；「显示更多」只控制此刻放进页面的卡片数量，避免 500 条收藏或 1000 条历史一次性撑大 DOM。
- 文案走 i18n（`action.backToTop`）：它同时是 `aria-label` 与 `title`，必须两种语言都有。底部那条「已显示 / 显示更多」仍是硬编码中文，与相邻区域保持一致。

## 已知坑 / 边界

- **`.library-batch-bar` 曾长期只有类名没有样式**（2026-09-22 补）：于是文字和按钮挤在一起、按钮被压得不像按钮。现在它是 flex + `space-between`（文字在左、按钮贴右），按钮用小号（`.btn small`），顶部一条发丝线与列表收口。
- 返回顶部依赖**页面滚动走 window**（当前 App 只有 window 滚动）。如果将来某个视图改成内部滚动容器（`overflow-y: auto`），这个按钮要跟着改成滚那个容器。
- 窄屏（≤900px）右下角是收藏夹归属弹层 `.membership-card` 的地盘，两者靠层级让位：弹层 `z-index: 25`、按钮 `20`，弹层打开时压住按钮。
- 显示阈值在 `ScrollToTopButton.tsx` 集中定义；搜索结果与历史页根据实际页面视图使用较低阈值，其他页面保留原有阈值。

## 测试怎么跑

- 契约（源码层，跑得快）：`tests/test_webui_ui_contract.py` 里的
  `test_scroll_to_top_button_is_mounted_and_wired`、`test_scroll_to_top_is_positioned_and_has_own_style`、
  `test_library_batch_bar_puts_show_more_on_the_right`、`test_all_bounded_long_lists_render_in_batches`。
- 真实渲染：`scripts/favorites_ui_smoke.py` 用隔离 SQLite + 构建产物跑浏览器，
  是最接近真实的一条，覆盖桌面、390px 收藏页与 320px 设置页。
- 搜索与历史：`scripts/scroll_to_top_smoke.py` 用模拟长列表跑浏览器，覆盖桌面与 390px、显示时机及点击回顶。
