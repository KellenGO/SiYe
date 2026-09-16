# 无窗口启动（托盘启动器）

## 一句话

双击 `四野.exe` → 不弹黑色控制台，后端静默运行，浏览器自动打开 `127.0.0.1:8080`，
右下角托盘图标可「打开四野 / 打开日志目录 / 退出四野」。

## 代码入口

| 职责 | 位置 |
|---|---|
| 统一入口 + 角色分发 + 单实例锁 + 托盘菜单 + 退出 | `tray_main.py` |
| 后端进程入口（uvicorn、路由注册） | `desktop_main.py`、`api/main.py` |
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
- 清理旧测试包时，将其收藏、登录资料和缓存备份到根目录 `data/version-backups/`，不覆盖常用 dist 的用户数据；此备份同样不能进入发布包。
- 发布包校验会**拒绝**含 `data` / `.cache` / `browser_data` 的产物（防止把本机登录态和个人收藏发出去）。
- 普通用户默认使用按当前用户安装的 Windows 安装器；便携 ZIP 作为备用。安装器与 ZIP 封装同一个 `dist/SiYe`，不会产生两套运行行为。

## 已知坑 / 边界

- **日志按天切但从不清理、也没有大小上限**，长期使用会无限累积（`data/logs/`）。
- 启动后立即二次双击会等待原后端就绪；等待超时才提示，不应把正常启动过程误报为失败。
- 控制管道不能用阻塞式标准输入读取：Windows 下会与 NumPy 原生模块初始化互相阻塞。已改成非阻塞探测，并用实际 EXE 检查启动及断管退出。
- 复用外部已有后端时，退出只关托盘、后端继续常驻（界面会提示「已有服务保持运行」）。
- `taskkill /F` 兜底**不会**触发 FastAPI 的 shutdown 清理（优雅通道成立时无碍）。
- 托盘菜单的真实鼠标操作与 Windows 窗口表现**仍需人工实机验收**；
  自动化只验证进程行为与编译配置（PE subsystem：`SiYe.exe=3` Console、`四野.exe=2` GUI）。
- 发布前必须**全新打包**，不要复用被运行过的 `dist/`（里面会带上用户数据）。

## 测试怎么跑

`tests/test_tray_launcher.py`（角色判定、命令构造、日志命名、健康识别、菜单文案、图标、单实例锁）。
打包后的验证见 `scripts/exe_clean_room_smoke.py`；安装器另由 `scripts/installer_clean_room_smoke.py` 验证静默安装、运行和卸载。
