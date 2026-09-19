# 功能地图

一行一个功能 → 详细文档 + 主要代码入口。**新增功能请在这里加一行。**
（项目背景、硬规则、环境坑见根目录 `AGENTS.md`。）

| 功能 | 文档 | 主要代码入口 |
|---|---|---|
| 可跳过的新手引导 | [features/getting-started.md](features/getting-started.md) | `webui/src/components/help/GettingStarted.tsx`、`webui/src/hooks/useOnboarding.ts` |
| 本机诊断报告与问题反馈 | [features/support-diagnostics.md](features/support-diagnostics.md) | `webui/src/lib/supportDiagnostics.ts`、`webui/src/components/help/SupportDiagnostics.tsx` |
| 本地收藏夹（内置分类 + 自建收藏夹） | [features/favorites-library.md](features/favorites-library.md) | `api/services/library_store.py`、`api/services/library_migration.py`、`webui/src/hooks/useBookmarks.ts` |
| 跨平台收藏同步 + 持久化 | [features/remote-favorites-sync.md](features/remote-favorites-sync.md) | `api/services/remote_favorites_store.py`、`api/services/favorites_job_manager.py` |
| 搜索体验（排序 / 去重 / 渐进展示 / 冷却） | [features/search-experience.md](features/search-experience.md) | `webui/src/lib/searchExperience.ts`、`webui/src/lib/platformMeta.ts`、`api/services/result_cache.py` |
| 环境自检（`/api/health` 与 Platform Doctor） | [features/environment-health.md](features/environment-health.md) | `api/services/environment_health.py`、`webui/src/components/search/PlatformStatus.tsx` |
| 无窗口启动（托盘启动器） | [features/tray-launcher.md](features/tray-launcher.md) | `tray_main.py`、`MediaCrawler.spec` |
| Windows 安装器（默认发布方式） | [features/windows-installer.md](features/windows-installer.md) | `installer/SiYe.iss`、`scripts/build_installer.ps1`、`.github/workflows/release-package.yml` |
| 登录与账号（扩展 / 扫码 / profile） | [features/extension-login.md](features/extension-login.md) | `api/services/accounts.py`、`aggregate_search/worker.py` |
| 许可与免责声明（首次启动接受门） | [features/license-disclaimer.md](features/license-disclaimer.md) | `webui/src/components/license/LicenseDisclaimer.tsx`、`webui/src/App.tsx` |
| 观看历史（自动记录点开看过的内容） | [features/watch-history.md](features/watch-history.md) | `api/services/watch_history_store.py`、`webui/src/components/history/HistoryPage.tsx` |
| 外观与主题色（8 种色 × 深浅） | [features/appearance-themes.md](features/appearance-themes.md) | `webui/src/store/themeStore.ts`、`webui/src/index.css` |

## 其他文档

- [`history/2026-09-15-合并后测试版交付.md`](history/2026-09-15-合并后测试版交付.md) —— 本地测试包、验证证据与待实测项目
- [`使用说明.md`](使用说明.md) —— 面向普通用户的操作步骤（网页版 `site/guide.html`）
- [`favorite-metrics.md`](favorite-metrics.md) —— 收藏指标补全的来源与字段
- [`plans/2026-09-18-更新方向与难度评估.md`](plans/2026-09-18-更新方向与难度评估.md) —— 后续更新清单、难度判断与排期
- [`plans/2026-09-18-三项执行方案.md`](plans/2026-09-18-三项执行方案.md) —— Installer 收尾 / 失败提示改弹窗 / 教程补平台勾选的具体做法
- [`plans/2026-09-18-主题色与观看历史.md`](plans/2026-09-18-主题色与观看历史.md) —— 8 种主题色与观看历史的做法与待确认点
- [`plans/2026-09-19-热搜榜方案.md`](plans/2026-09-19-热搜榜方案.md) —— 热搜榜的定位、接口可行性 spike 与分步做法
- [`plans/`](plans/) —— 一次性方案与改造计划（**不是当前行为的依据**，看功能 wiki）
- [`history/2026-09-14-三项功能审查与修复.md`](history/2026-09-14-三项功能审查与修复.md) —— 历史修复记录，当前行为以功能 wiki 为准
- [`repository-map.md`](repository-map.md) —— 目录用途、唯一启动入口与整理边界
- [`CHANGELOG.md`](../CHANGELOG.md) —— 版本更新记录
- [`decisions/`](decisions/) —— 决策记录：为什么这么选

## 发布链路（与功能无关但常要用）

`scripts/build_exe.ps1` → `scripts/package_exe.py`（校验 + 便携 ZIP）→ `scripts/build_installer.ps1`
→ `scripts/exe_clean_room_smoke.py` / `scripts/installer_clean_room_smoke.py`
→ `.github/workflows/release-package.yml`。落地页与使用说明网页在 `site/`（GitHub Pages 自动部署）。
