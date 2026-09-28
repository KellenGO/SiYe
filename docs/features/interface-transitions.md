# 主要页面与内容视图切换动效

## 一句话

通过短促的页面切入和滑动选中底板，让主导航与列表／网格切换更容易辨认当前状态。

## 代码入口

| 职责 | 位置 |
|---|---|
| 主导航切页时标记新页面 | `webui/src/App.tsx` 的 `App` |
| 共用列表／网格开关 | `webui/src/components/search/ResultTabs.tsx` 的 `ResultTabs` |
| 动效与减少动态效果规则 | `webui/src/index.css` |

## 关键决定

- 只在主导航切换到另一个页面时播放一次轻量切入；首次打开、设置内部分区切换和搜索首页／结果视图往返不播放。页面内容立即更新，顶栏与背景保持稳定。
- 设置页继续保留挂载以维持原有轮询；动效仅作用于其可见容器。内容视图共用开关滑动选中底板，原有各页面偏好、筛选和已显示条数仍由原组件管理。
- 动效使用现有 CSS；系统要求减少动态效果时关闭页面切入和选中底板的过渡。

## 已知坑 / 边界

- 页面切入容器只做短暂透明度和位移变化，结束后不保留 transform，避免影响页面内固定定位的弹层。
- 列表／网格内容会立即按原布局切换；不对大量卡片逐个做入场动画，以免长列表产生闪烁或迟滞。

## 测试怎么跑

- 前端：在 `webui/` 执行 `npm run build`、`node node_modules/typescript/bin/tsc -p tsconfig.test.json` 和 `node run-compiled-tests.mjs`。
- 浏览器：`scripts/scroll_to_top_smoke.py` 用模拟结果检查列表／网格与窄屏操作；另在桌面和窄屏检查主导航往返及 `prefers-reduced-motion` 下的动效关闭。
