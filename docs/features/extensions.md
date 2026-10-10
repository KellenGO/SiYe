# 扩展管理

## 一句话

在「设置 → 扩展管理」按需下载、安装、开启、关闭及卸载官方 AI 研究助手；基础软件不带模型 SDK 和 Claude 运行程序。

## 代码入口

| 职责 | 位置 |
|---|---|
| 官方发布查询、下载校验、安全解压与安装切换 | `api/services/extensions.py` 的 `Extensions`、`extract_archive` |
| 本机管理接口与受限界面资源 | `api/routers/extensions.py` 的 `extensions_router` |
| 设置入口、扩展卡片和进度 | `webui/src/components/accounts/ExtensionsPage.tsx` |
| 状态查询与界面桥接 | `webui/src/hooks/useExtensions.ts`、`webui/src/components/extensions/PluginUI.tsx` |
| 资料收集和独立分析进程 | `api/services/research_jobs.py` 的 `task_command`、`ResearchJobs.process` |
| 基础发布包排除 AI 依赖 | `MediaCrawler.spec`、`scripts/build_exe.ps1` |

## 关键决定

- 第一版只支持官方 SiYe-AI 扩展，下载来源固定为官方 GitHub Release。匿名 API 限流时读取同一官方 Release 的版本清单，并按相同规则核对地址、版本、大小与 SHA256，不要求 GitHub 登录。浏览器式管理页是统一入口，不开放任意地址安装或第三方代码市场。
- 首次安装默认关闭；开启后出现空间 AI 入口和服务配置。停用结束正在运行的任务，关闭聊天面板仍保留任务。配置与聊天历史沿用旧路径，无需复制或迁移；已保存配置不会让首次安装自动开启。
- 卸载默认只移除扩展程序；用户可勾选清除 AI 配置和聊天记录。空间快照、收藏、笔记及已写入的 AI 回答保留。
- 下载根据可信发布资产的 SHA256 校验，包内声明支持的主程序版本。下载和解压完成后才切换版本，失败保留原安装；用户可取消下载及安装。损坏或不兼容的插件不会显示为已开启。
- 插件自带独立 Windows 运行程序和编译后的界面，不要求用户安装 Python 或 Node。平台账号和取数由主程序管理；分析进程只接收本次资料。界面通过笔记桥接请求写入，不能直接访问笔记编辑器。取消沿用 Windows 子进程树管理。长期取舍见[官方 AI 可选扩展](../decisions/2026-10-10-官方AI可选扩展.md)。

## 已知坑 / 边界

- GitHub 下载受用户网络环境影响，离线导入和镜像暂未开放。没有兼容的正式发布资产时显示获取失败，不把本机开发文件冒充可下载版本。
- Windows 短暂占用安装目录时会有限重试；持续占用时安装失败并保留旧版本，卸载失败保留可重试的关闭状态。
- 本机扩展目录与软件目录分离，关闭主程序会取消未完成的下载与任务；完成的安装和开关在重启后恢复。安装资源与个人 AI 数据分开，删除扩展不会清除笔记。
- 独立运行程序属于用户账号下的本机进程，并不是操作系统权限沙箱；第一版只接受官方发布资产。
- 聊天、配置和模型协议的回归在独立仓库维护，主仓库负责管理与接口集成。两个仓库须分别发布；插件版本范围不兼容时必须拒绝启用。

## 测试怎么跑

- 后端：pytest tests/test_extensions.py tests/test_research.py tests/test_research_acquisition.py tests/test_reading.py tests/test_reading_platforms.py，使用每次新的项目内 --basetemp 子目录。所有安装、卸载和历史测试均使用临时目录。
- 前端：webui 中 npm run test:search 和 npm run build。
- 管理流程：scripts/extensions_ui_smoke.py --package 指向独立插件 ZIP，通过模拟官方下载响应在临时目录验收下载、默认关闭、开启、停用及卸载保留笔记。
- 助手集成：SIYE_AI_SOURCE 指向插件源码目录，运行 scripts/research_ui_smoke.py；独立 worker 使用 scripts/research_ai_smoke.py --exe 验证。
