# 仓库目录与日常入口

日常测试使用 `dist/SiYe/四野.exe`，根目录 `MediaCrawler.bat` 优先打开它；`SiYe.exe` 是托盘和工作进程使用的后端程序。`启动-源码.bat` 是独立的源码开发入口，默认 8090；直接运行 `scripts/start.ps1` 默认 8080。多工作区开发用 `SIYE_PORT` 显式区分端口。

| 位置 | 放什么 |
| --- | --- |
| `api/`、`aggregate_search/`、`media_platform/` | API、任务编排与平台实现；共享机制与平台请求上下文位于 `api/services/`、`base/` |
| `base/`、`config/`、`tools/` 等 Python 目录 | 公共运行支持与原有依赖；不要仅凭目录名判断无用 |
| `webui/src/`、`webui/tests/` | 应用前端和测试 |
| `browser_extension/` | 可选浏览器登录同步扩展 |
| `assets/` | 程序图标等发布资源 |
| `site/` | 对外介绍页与使用指南 |
| `style-preview/` | UI 参考稿，保留供设计对照，不是运行入口 |
| `scripts/`、`tests/` | 可重复执行的构建、诊断与测试；一次性代码编辑脚本不保留在前端目录 |
| `docs/features/` | 当前功能 wiki |
| `docs/decisions/`、`docs/history/` | 取舍与历史记录，历史记录不代表当前验收状态 |
| `.workbuddy/memory/` | 可选的本机笔记，不入库也不承担交接 |
| `build/`、`dist/`、`.tmp_pytest_*/` | 可重建的构建、运行及测试产物；不要提交 |
| `.tmp_*/`、根目录 `.tmp_*.py`、`webui/.test-dist/` | 临时验证与一次性诊断产物；确认无在用进程、Git worktree、共享依赖链接或真实用户数据后才可清理 |
| `data/`、`browser_data/`、`.cache/` | 用户收藏、登录资料及缓存；不能当作普通构建垃圾清理 |

源码运行和 EXE 运行的数据目录不同。旧测试包资料保存在根目录 `data/version-backups/`，未自动合并数据库；发布包必须从干净目录构建，不能把日常使用后的 dist 直接上传。

`cache/` 是受跟踪的 Python 缓存实现，`.cache/` 才是本机缓存；`build/tools/` 是本机构建工具，AI 运行程序由独立 SiYe-AI 扩展仓库构建，`.venv/` 与 `webui/node_modules/` 是开发依赖。它们不能仅因未进入 Git 就当作垃圾。根目录 `.tmp_*` 的忽略规则已入库，不再依赖某一台机器的 `.git/info/exclude`。

V1.2 发布状态与尚待验收的边界见 [发布检查](releases/v1.2.0.md)。当前 `dist/` 仍是旧版本日常入口，准备候选包时使用独立目录，不覆盖已有运行环境。

## 本地瘦身

使用 `.venv/Scripts/python.exe scripts/cleanup_workspace.py` 预览；加 `--apply` 才删除白名单内的已退役构建、旧安装器副本、pytest 临时目录和源码编译缓存。加 `--include-browser-caches` 才纳入四个平台 profile 的下载及渲染缓存。不要用 `git clean -fdx` 替代此入口。

脚本保留开发依赖、`build/tools/`、常用 `dist/SiYe/`、全部用户数据和版本备份，以及最新发布 ZIP 与校验文件（正式 V1.2.0 ZIP 与 SHA256 在 `build/ai-extension/dist/`，验收日志在 `build/v1.2-release/`）。旧的内置 AI 运行程序 `agent_runtime/` 已退役，当前 AI 程序由独立扩展提供。未来新建的未知构建目录只报告，不自动删除；白名单中的旧候选路径不要重复用于新构建。

清理前检查 Git 跟踪文件、已登记 worktree、嵌套 Git 仓库、个人数据库和进程引用。无法读取或确认进程状态时跳过；不要为腾空间修改目录权限或关闭用户浏览器。pytest 目录只允许清理由 `test_*` 子目录明确标识的测试数据库。目录联接只删除链接本身，不遍历目标；登录 Cookie、配置、Local Storage、IndexedDB 和 Service Worker 存储保留。

当前已发布 V1.2.0；本机只保留正式 ZIP 与 SHA256，验收时解压到新的临时目录；旧构建环境和重复解包目录需要时按锁文件重建。关键验收日志、清单和未解决的依赖 audit 记录继续保留。日常 `dist/SiYe/` 仍为 V1.0.0，没有被发布构建覆盖。清理脚本的隔离测试在 `tests/test_cleanup_workspace.py`。

发布构建统一使用 `scripts/build_exe.ps1` → `package_exe.py` → `build_installer.ps1`；旧源码打包与对应 smoke 已退役。`build_exe.ps1 -OutputDir build/<候选名>/dist -PythonPath .venv/Scripts/python.exe` 可构建独立候选，默认输出仍为 `dist/`。开发用的文件头检查与 Cookie smoke 保留源码，只从 EXE 排除；CI 检查现有锁文件并按锁文件安装，不重新选择依赖版本。

其他 worktree 的实际位置和分支用 `git worktree list` 核对，不按旧目录名猜测。接手前检查其状态；若有未提交文件，先确认归属，不清理或覆盖。合并后继续开发从最新 `master` 开始。

## 本机环境

- **bash 里跑 npm 会被拦，PowerShell 里完全不会。** 真正被拦的是 `wsl.exe`（安全中心的程序黑名单）：
  bash 执行 `npm` 会解析到无扩展名的 Unix shell 脚本（`C:\Program Files\nodejs\npm`，与
  `npm.cmd` / `npm.ps1` 并列），进而触发 WSL，于是报 "PROGRAM BLOCKED BY SECURITY POLICY"。
  **PowerShell 里 npm 是好的**：`npm --version` = 10.9.7、`npm view <pkg> version` 能连 registry、
  `npm install --save-dev <pkg>` 也能跑。所以前端命令**用 PowerShell 跑就行**，
  不必绕 node 绝对路径。`scripts/build_exe.ps1` 是 .ps1，里面的 `npm ci` / `npm run build` 同样没事。
- **同一个根因让 bash 工具基本不可用**：WorkBuddy 的 bash shim
  `shell-runtime-bash-env.sh` 第 3 行 `dirname` 就失败，PATH 整个是坏的，
  于是 `grep` / `find` / `head` 全部 "command not found"。**排查环境时别指望 bash，用 PowerShell。**
- **pytest 写不进系统 Temp**，必须带项目内临时目录：`--basetemp=.tmp_pytest_xxx`。
  **而且每次都要换新的子目录**（`--basetemp=.tmp_pytest_run/r<时间戳>`，父目录先建好）——
  复用同一目录会触发沙箱的 safe-delete 批量保护，报成上百个 setup ERROR 的**假回归**。
- **全量 pytest 会超过命令的默认超时（120 秒）**：套件已经涨到 1000+ 个用例，
  实测一批就要 107 秒，**整跑会在跑到 80% 左右被掐断，而且看不到任何失败信息**
  （表现为 exit 1 + 输出停在半路，很容易误判成"某个测试崩了"）。
  两个办法：跑的时候显式给更长的 timeout，或者**按文件分两批跑**
  （用 `--ignore=<后半批文件>` 跑前半，再单独跑后半），合起来覆盖全部用例。
  `.tmp_*` 已在受跟踪的 `.gitignore` 里，临时脚本不会被误提交。
- **shell 会 mangle 带斜杠的参数**：`git branch feat/x` 会静默失败并报 `fatal: invalid reference`。
  **可靠做法：用 Python `subprocess.run(['git', ...])` 调 git**（这一轮全程这么做，稳定），
  分支名用连字符。
- **Bash 工具的双引号里不要出现反引号**：bash 会当命令替换执行、静默吃掉内容。
  复杂脚本一律用 Write 落盘再跑。
- Python 用 `.venv/Scripts/python.exe`（该 venv 由 uv 建，原本没有 pip）。
- Windows 临时 worktree 不用目录联接（junction）共享主工作区的 `.venv/` 或 `webui/node_modules/`：归档／清理可能删除链接目标。各 worktree 使用独立开发依赖；已有共享链接须先分离并确认目标完整，再归档。
- **前端 `src/lib` 之间的 import 必须带 `.js` 后缀**（编译产物是原生 ESM）：
  `tsc` 不会报错，只有 `run-compiled-tests.mjs` 会以 `ERR_MODULE_NOT_FOUND` 暴露。
- **`git pull/push` 需要本机代理 `127.0.0.1:7890` 在跑**（git 里配了 `http.proxy`）；
  代理没起时会报 "Failed to connect to github.com port 443"。离线时用
  `git bundle create <项目外的路径>.bundle <branch>` 做本地备份。
- **代理起来了 push 仍可能失败**：`credential.helper` 里有 `manager`（Git Credential Manager），
  它在无交互环境会尝试弹窗、然后 git **静默返回 128 且不打印任何错误**（`git ls-remote` 却正常，
  因为读操作不要凭据 —— 很容易误判成网络问题）。可靠推法是绕开它、直接用 gh 的 token：

  ```powershell
  $t = (gh auth token).Trim()
  git push "https://$t@github.com/<owner>/<repo>.git" <branch>
  ```

  另外 **PowerShell 下 git 的 stderr 会被转成 ErrorRecord**，`git ... 2>&1 | Out-File` 经常拿到空文件，
  排查时先把结果存进变量（`$r = git ... 2>&1`）再写文件。`cmd /c` 在本工具里被禁，别指望它。
- **agent 沙箱里 `refs/remotes/**` 可能写不进去**：`git fetch` 会报成功，
  但 `git branch -vv` 里上游仍显示 `[origin/xxx: gone]`、`git rev-parse origin/master` 报
  `fatal: Needed a single revision`。**这是沙箱的限制，不是仓库损坏**（对照实验：写到
  `refs/tags/` 能持久、Python 直接写文件也成功，只有 `refs/remotes` 被丢弃）。
  要拉取更新就用 `git fetch origin <branch>` + `git merge FETCH_HEAD`，别依赖 remote-tracking ref。
