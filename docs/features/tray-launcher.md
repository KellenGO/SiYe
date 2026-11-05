# 无窗口启动（托盘启动器）

## 一句话

双击 `四野.exe` → 不弹黑色控制台，后端静默运行，浏览器自动打开 `127.0.0.1:8080`，
右下角托盘图标可「打开四野 / 打开日志目录 / 退出四野」。

## 代码入口

| 职责 | 位置 |
|---|---|
| 统一入口 + 角色分发 + 单实例锁 + 托盘菜单 + 退出 | `tray_main.py` |
| 后端进程入口（uvicorn、路由注册） | `desktop_main.py`、`api/main.py` |
| 源码双击入口（解释器选择、端口绑定预检、等待就绪） | `启动-源码.bat` |
| 打包配置（两个 EXE 共用 `tray_main.py`） | `MediaCrawler.spec` |
| 发布包校验 + 便携 ZIP | `scripts/package_exe.py` |
| 安装器 | `installer/SiYe.iss`、`scripts/build_installer.ps1` |
| 一键构建 | `scripts/build_exe.ps1` |
| 干净环境冒烟（产物 / 运行 / worker 协议 / 正常退出） | `scripts/exe_clean_room_smoke.py` |

## 关键决定

- **两个 EXE 分工，不能合并**：
  - `SiYe.exe`（`console=True`）：后端 + 平台 worker。**必须保留控制台** ——
    worker 子进程经 `sys.executable` 拉起，靠 **stdin/stdout 管道**通信，改成无窗口会破坏通信。
  - `四野.exe`（`console=False`）：托盘启动器，用 `CREATE_NO_WINDOW` 隐藏拉起上面那个。
- 单实例：`data/launcher.lock` 文件锁；第二次双击只打开已有页面。
- 启动时若 `8080` 已有本项目后端则复用，不重复启动。
- 退出优先走**优雅通道**（关 stdin → `PeekNamedPipe` 轮询探测写端关闭 → 通知后端收尾），
  超时才 `taskkill /F /T` 强杀整个进程树。
- 后端日志写 `data/logs/backend-<日期>.log`。
- 本机测试统一使用 `dist/SiYe/四野.exe`，根目录 `MediaCrawler.bat` 优先打开这一份。没有打包产物时才回退到 `启动-源码.bat`；该源码入口默认 8090，产品默认 8080。直接使用 `scripts/start.ps1` 时默认仍为 8080，并行开发须显式区分端口。
- 源码 BAT 在启动后端前实际尝试绑定端口：Windows 拒绝绑定（例如 WinError 10013）时直接提示原因和 `SIYE_PORT` 换端口的方法，不进入就绪等待，也不自动修改端口。解释器路径支持空格，目录内 `.venv` 优先于系统 Python。
- 清理旧测试包时，将其收藏、登录资料和缓存备份到根目录 `data/version-backups/`，不覆盖常用 dist 的用户数据；此备份同样不能进入发布包。
- 发布包校验会**拒绝**含 `data` / `.cache` / `browser_data` 的产物（防止把本机登录态和个人收藏发出去）。
- 普通用户默认使用按当前用户安装的 Windows 安装器；便携 ZIP 作为备用。安装器与 ZIP 封装同一个 `dist/SiYe`，不会产生两套运行行为。
- [空间 AI 研究](space-research-ai.md) 的 SDK 和官方运行程序随包分发；研究任务经同一后端入口启动隔离 worker，退出时一并清理。构建入口先准备并校验固定版本运行程序，用户无需另装 Claude Code。

- **开屏预热平台 worker**（2026-11-05）：冷启动要起一个 Python 解释器，四个平台叠加
  就是可感知的"第一次搜特别慢"。startup 时用后台任务把各平台常驻 worker 先起好，
  真正的搜索仍走 `submit()`、命中已有进程。**尽力而为**：预热失败只记一行 info，
  不影响任何功能（搜索会自己补启动），也不阻塞健康检查与首屏。

## 已知坑 / 边界

- **日志按天切但从不清理、也没有大小上限**，长期使用会无限累积（`data/logs/`）。
- 没有监听进程不代表端口能绑定；Windows 的端口限制也会拒绝访问。源码入口的预检只反映检查当时的状态，不能保证随后启动时端口仍可用；浏览器扩展仍只支持 8080。
- 启动后立即二次双击会等待原后端就绪；等待超时才提示，不应把正常启动过程误报为失败。
- 控制管道不能用阻塞式标准输入读取：Windows 下会与 NumPy 原生模块初始化互相阻塞。已改成非阻塞探测，并用实际 EXE 检查启动及断管退出。
- 复用外部已有后端时，退出只关托盘、后端继续常驻（界面会提示「已有服务保持运行」）。
- `taskkill /F` 兜底**不会**触发 FastAPI 的 shutdown 清理（优雅通道成立时无碍）。
- 托盘菜单的真实鼠标操作与 Windows 窗口表现**仍需人工实机验收**；
  自动化只验证进程行为与编译配置（PE subsystem：`SiYe.exe=3` Console、`四野.exe=2` GUI）。
- 发布前必须**全新打包**，不要复用被运行过的 `dist/`（里面会带上用户数据）。

## 测试怎么跑

`tests/test_tray_launcher.py`（角色判定、命令构造、日志命名、健康识别、菜单文案、图标、单实例锁）。
`tests/test_source_launcher.py` 在 Windows 实际执行 BAT，覆盖含空格和中文的路径、非法端口、模拟的 Windows 绑定拒绝，以及一次性模拟后端的启动与就绪等待；不启动真实后端、不接触用户数据。
打包后的验证见 `scripts/exe_clean_room_smoke.py`；安装器另由 `scripts/installer_clean_room_smoke.py` 验证静默安装、运行和卸载。
