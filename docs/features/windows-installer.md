# Windows 安装器

## 一句话

普通用户默认下载 `SiYe-Setup-Windows-x64.exe`，按向导安装后从开始菜单或桌面启动四野；便携 ZIP 继续保留给需要免安装运行的用户。

## 代码入口

| 职责 | 位置 |
|---|---|
| Inno Setup 安装定义 | `installer/SiYe.iss` |
| 安装器构建与 SHA256 | `scripts/build_installer.ps1` |
| 安装、运行、卸载烟测 | `scripts/installer_clean_room_smoke.py`、`scripts/exe_clean_room_smoke.py` |
| EXE / ZIP / 安装器总构建 | `scripts/build_exe.ps1`、`scripts/package_exe.py` |
| 发布与 Release 资产 | `.github/workflows/release-package.yml` |

## 关键决定

- 默认按当前用户安装到 `%LOCALAPPDATA%\Programs\SiYe`，不请求管理员权限；同一 `AppId` 让后续版本原地升级。
- 开始菜单快捷方式固定创建，桌面快捷方式默认不勾选；安装结束可以直接启动托盘版 `四野.exe`。
- 安装器只封装已经通过发布校验的 `dist/SiYe`，不能包含 `data`、`.cache` 或 `browser_data`。卸载器只移除安装清单中的程序文件，不主动删除运行后产生的收藏、登录资料和日志。
- GitHub Release 以安装器为主资产，同时保留 ZIP 与两种产物各自的 SHA256。原因与取舍见[Windows 安装器发布决策](../decisions/2026-09-16-Windows安装器作为主发布方式.md)。

## 已知坑 / 边界

- 安装器和两个 EXE 目前没有商业代码签名证书，Windows SmartScreen 可能显示「未知发布者」；只应从项目官方 Release 下载并可用 SHA256 校验。
- 登录 profile 和日志仍沿用安装目录内的运行时目录。覆盖升级会保留这些目录；标准卸载不会主动擦除它们，因此卸载后安装目录可能因用户数据而保留。
- Inno Setup 只是构建机依赖。普通用户不需要安装它；本机构建需 Inno Setup 6.3+，也可用 `INNO_SETUP_COMPILER` 指定 `ISCC.exe`。
- 简体中文消息文件固定在 `installer/languages/`，避免本机与 CI 的 Inno Setup 语言组件不同导致构建失败；来源和许可证保存在同目录。
- 当前只有 Windows x64 安装器；Mac、Linux 和 Windows ARM 原生包不在本轮范围内。

## 测试怎么跑

- 静态契约：`tests/test_installer_contract.py`、`tests/test_docs_wiki.py`。
- 构建：`powershell -ExecutionPolicy Bypass -File scripts/build_installer.ps1 -PythonPath .venv/Scripts/python.exe`。
- 产物：`.venv/Scripts/python.exe scripts/installer_clean_room_smoke.py --installer dist/SiYe-Setup-Windows-x64.exe`，会在临时目录静默安装、跑完整 EXE clean-room、静默卸载。
