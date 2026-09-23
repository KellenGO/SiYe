# 外观与主题色

## 一句话

「外观与首页」里可以选 8 种主题色，**主题色只管色相，深浅仍然是独立的开关**（浅色 / 深色 / 跟随系统），两者相乘。

## 代码入口

| 职责 | 位置 |
|---|---|
| 深浅与主题色的状态、持久化 | `webui/src/store/themeStore.ts`（`useThemeStore`，`ACCENTS` 列表与色块值） |
| 深浅的 CSS | `webui/src/index.css` 的 `:root` 与 `.dark` |
| 每种主题色的 CSS（覆盖品牌色那一组变量） | `webui/src/index.css` 的 `html[data-accent="…"]` 与 `html.dark[data-accent="…"]` |
| 选择器界面 | `webui/src/components/accounts/AccountsPage.tsx` 的外观与首页区 |
| Tailwind 侧的颜色映射 | `webui/tailwind.config.ts` |

## 关键决定

- **主题色只覆盖品牌色，不碰中性色**：每个 accent 只重写 `--brand` / `--brand-2` / `--brand-soft` /
  `--brand-ink`（外加 `--primary` / `--accent` / `--ring`、`--cyber-neon-cyan*`、
  `--cyber-border-glow`、`--siye-brand*`）。背景、正文、边框与**平台自身颜色**（小红书红、抖音黑）
  一律不随主题色变化。
- **主题色不带深浅**：选「石墨」不会把界面变黑，只是把强调色换成中性灰；想变黑要另外切深色。
  8 个色里若有暗色系，选完就黑屏会很突然。
- 偏好存在两个 localStorage 键：`mediacrawler_theme`（深浅）与 `mediacrawler_accent`（主题色），
  都按浏览器保存；新用户默认湛蓝 + 浅色。
- 深色值不是把浅色值反过来：同一个色相在深底上要另给一套值（更亮、饱和度略降），
  所以每种主题色有两段 CSS。
- **入口来回都落在顶栏同一个位置**（`webui/src/components/layout/Header.tsx`）：首页右上角是
  「自定义」（进外观设置），进到外观设置后同一位置换成「回首页」（`onNavigate('search')`）。
  出去和进来的按钮在同一个坐标上，不用先滚到页面底部找出口；设置页底部原来那个
  「回首页看看」已经去掉。颜色、账号等其它设置区不显示这个按钮（顶栏主导航里有「首页」）。
- 设置分类在普通窄屏横向排列，在 340px 以下改成两列换行，避免最后一个入口被屏幕边缘裁掉。

## 已知坑 / 边界

- 颜色仍散落在几处，尚未全部收敛到变量：`webui/src/index.css` 里还有约 60 处直接使用色值，
  `Header.tsx` / `AccountsPage.tsx` / `LicenseDisclaimer.tsx` 里也各有几处。
  换主题色时如果某处「没跟着变」，八成是那里还写着硬编码色值。
- 8 色 × 深浅的对比度只能人眼确认，没有自动化检查。
- 主题色不影响搜索结果与账号状态，也不参与诊断报告。

## 测试怎么跑

- 前端：`node node_modules/typescript/bin/tsc -p tsconfig.test.json` + `node run-compiled-tests.mjs`。
- 视觉：起后端后到「设置 · 外观与首页」逐个点色块，浅色与深色各看一遍。
