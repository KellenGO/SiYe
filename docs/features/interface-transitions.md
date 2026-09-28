# 主要页面与内容视图切换动效

## 一句话

通过短促的页面切入和滑动选中标记，让主导航、平台页签、设置分区与列表／网格切换更容易辨认当前状态。

## 代码入口

| 职责 | 位置 |
|---|---|
| 主导航切页时标记新页面 | `webui/src/App.tsx` 的 `App` |
| 跟随选中按钮尺寸与位置的标记 | `webui/src/hooks/useSlidingIndicator.ts` 的 `useSlidingIndicator` |
| 顶栏导航选中线 | `webui/src/components/layout/Header.tsx` 的 `Header` |
| 设置分区选中底色及内容 | `webui/src/components/accounts/AccountsPage.tsx` 的 `AccountsPage` |
| 共用列表／网格开关 | `webui/src/components/search/ResultTabs.tsx` 的 `ResultTabs` |
| 动效与减少动态效果规则 | `webui/src/index.css` |

## 关键决定

- 主导航切到另一个页面时播放轻量切入，导航选中线平滑移到新位置；搜索首页／结果视图往返仍按原逻辑处理。页面内容立即更新，顶栏与背景保持稳定。
- 搜索结果、历史与收藏共用的平台页签让选中线跟随平台名称滑动，线条在名称两侧略微延伸；数量仍在名称旁显示，但不参与下划线的定位和宽度。收藏页的本地／跨平台切换也保留同样的选中线。设置页三个分区让选中底色滑动，分区内容短暂淡入。按钮宽度或窄屏排列变化时，标记跟随实际位置，不假定固定间距。
- 设置页继续保留挂载以维持原有轮询；内容视图共用开关滑动选中底板，原有各页面偏好、筛选和已显示条数仍由原组件管理。
- 动效使用现有 CSS；系统要求减少动态效果时关闭页面切入、设置内容淡入和选中标记的过渡。

## 已知坑 / 边界

- 页面切入容器只做短暂透明度和位移变化，结束后不保留 transform，避免影响页面内固定定位的弹层。
- 列表／网格内容会立即按原布局切换；不对大量卡片逐个做入场动画，以免长列表产生闪烁或迟滞。
- 平台页签和设置分区仍立即切换内容；滑动的只是选中标记，不延迟筛选或设置响应。

## 测试怎么跑

- 前端：在 `webui/` 执行 `npm run build`、`node node_modules/typescript/bin/tsc -p tsconfig.test.json` 和 `node run-compiled-tests.mjs`。
- 浏览器：`scripts/scroll_to_top_smoke.py` 用模拟结果检查列表／网格与窄屏操作；另在桌面和窄屏检查主导航、平台页签、设置分区往返，以及 `prefers-reduced-motion` 下的动效关闭。
