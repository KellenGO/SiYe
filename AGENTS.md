# AGENTS.md — 先读这里

给所有在这个仓库里干活的 agent（人或 AI）的入口。**读完这一页，再去读 `docs/index.md`。**

## 这个项目是什么

「四野」：本地运行的中文社交平台聚合搜索工具。FastAPI 后端（`api/`）+ React 前端（`webui/`）
+ Playwright 抓取（`aggregate_search/`、`media_platform/`）。只监听 `127.0.0.1:8080`，
用使用者本人的登录态去搜小红书 / 抖音 / B站 / 知乎。打包成两个 EXE 分发。

## 知识库怎么用（两层，别搞混）

| 层 | 位置 | 性质 | 写什么 |
|---|---|---|---|
| 流水账 | `.workbuddy/memory/YYYY-MM-DD.md` | 按天、只追加 | 今天做了什么、踩了什么坑 |
| 功能 wiki | `docs/features/*.md` | 按功能、持续更新 | 这个功能是什么、入口在哪、为什么这样做、边界在哪 |

- 想了解某个功能，**读 `docs/features/<功能>.md`**，不要靠翻流水账。
- **一个功能一个文件**：改功能时顺手更新对应那一份。
- **别在 wiki 里复述代码**（签名、字段表、配置项一律不写）——只写从代码里读不出来的东西。
- 重要的取舍写进 `docs/decisions/`。

## 完成定义（DoD）——说"干完了"之前逐条过

改完任何功能，**在把任务标记为完成之前**必须满足：

- [ ] 相关测试通过（后端 `pytest --basetemp=.tmp_pytest_xxx`；前端 tsc + 测试）。
      本机具体命令见 `docs/features/search-experience.md` 的「测试怎么跑」。
- [ ] **更新 `docs/features/<功能>.md`**：入口变了改「代码入口」，做法变了改「关键决定」，
      修掉或新发现的坑改「已知坑 / 边界」。
      （纯内部重构、对外行为没变就跳过——别为了改而改。）
      **「代码入口」里不要写行号**（必漂移），只写文件路径 + 函数名。
- [ ] 做了取舍 → 在 `docs/decisions/` 加一条（背景 / 选项 / 决定 / 后果 + 状态），
      格式照 `docs/decisions/2026-09-14-登录方式取舍.md`。
- [ ] 加了新功能 → 在 `docs/index.md` 加一行，并按 `docs/features/_TEMPLATE.md` 新建一份。
- [ ] 在本机流水账 `.workbuddy/memory/YYYY-MM-DD.md` 追加当天记录（按日期，只追加）。
      **注意：`.workbuddy/` 被 `.gitignore` 忽略，所以这只对本机有意义、评审时看不到**；
      要让别人（或下一个 agent）看到的东西，必须写进 `docs/`。
- [ ] **改动已经提交**（见「硬规则」里那条"不许跨会话留未提交"）。
- [ ] **施工过程没有写进代码注释**：不要往代码里加 `Round 12`、`第 3 轮`、`Phase 4.2`
      这类"我是第几轮做的"标记 —— 那是 **commit message 的内容，不是代码的内容**。
      它对新读者零信息量，却在 67 个文件里累积了 200+ 处噪音。
      （存量不专门清理，**改到哪清到哪**；新写的代码一律不加。）

`tests/test_docs_wiki.py` 会守住**结构**（索引链接、必备小节、地图与文件一一对应），
以及**「代码入口」里写的文件是否真的存在**（2026-09-14 起）——
后者能自动抓住改名/移动后的文档漂移。它**不检查内容是否写对**、
也不检查"某个功能是不是压根没文档"，所以上面这份清单是**收尾动作，不是建议**。

## 写测试时的约定

- **假浏览器对象只有一处**：`tests/fixtures/browser.py` 里的
  `FakeBrowserContext` / `FakePage` / `FakePlaywright` 是超集替身，各测试原先的差异
  都做成了构造参数（预设 page、`navigated` 后换 cookie、`strict_closed` 关闭后读 cookie 抛错、
  `evaluate_result` 指定 evaluate 返回值、`goto_urls`/`goto_args` 记录导航）。
  **新写测试不要再抄一份 `_FakeCtx`/`_FakePW`** —— 2026-09-15 之前 9 个文件各有一份。
  带业务行为的假件（`_FakeDouYinClient`、`_FakeCrawler` 等）留在各自测试里，那些是被测逻辑本身。
- 拼错 i18n 键不会有运行时错误（i18next 直接把键名渲染出去），所以
  `tests/test_webui_ui_contract.py` 里有键存在性与多语言对齐的守卫。

## 硬规则

- **绝不提交 `data/`、`browser_data/`**：`data/` 是本机收藏库与日志，`browser_data/` 是各平台登录
  profile（**含 cookie**）。发布包必须排除，`scripts/package_exe.py` 会校验并拒绝。
- 需要写库的测试一律用临时目录，别碰真实用户数据。
- **不许跨会话留下未提交的改动**：收工前要么提交，要么在交付说明里写清"为什么不能提交、下一手该拿它怎么办"。
  长期挂着的未提交改动是**多 agent 协作里最贵的债** —— 下一个 agent（或下一次会话的自己）
  读到的是一份"半成品事实"，而它看起来和已完成的工作一模一样。
- **结构化重构与行为改动分船**：把"搬函数边界 / 抽公共层 / 改名"单独做成一个分支或至少一个独立提交，
  不要和行为改动混在一起。分开之后，与别人并行改动撞车时冲突会停在**文本级**；
  混在一起就变成**语义级**冲突 —— git 只会说"这两个 hunk 撞了"，帮不上忙。

## 文件所有权（并行开发时的互斥表）

**同一时刻只允许一个 agent 在一个路径范围内有未提交改动。** 开工前先看这张表，收工后更新它。

| 范围 | 归谁 | 说明 |
|---|---|---|
| 主目录 `MediaCrawler-main/` 的**全部**改动 | 主工作区负责人 | master 上不要出现两个来源的未提交改动 |
| 支线目录 `MediaCrawler-side-tasks/` | 支线负责人 | 开工前 `git status` 必须干净 |

**历史上最容易撞车的文件**（改它们之前先确认对方没有在改）：
`api/routers/search.py`、`api/services/accounts.py`、`api/services/search_job_manager.py`、
`api/services/favorites_job_manager.py`、`media_platform/xhs/client.py`、`webui/src/components/accounts/AccountsPage.tsx`。

**并发改同一批文件时，约定"结构按重构方、行为按改动方"来解冲突**，
并且合并方向固定为：**在功能分支上 `git merge master`，解完跑全量测试，绿了再快进合回 master**。
这样 master 全程可发布。

## 在这台机器上干活（环境坑，都踩过了）

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
  `.tmp_*` 已在 `.git/info/exclude` 里，临时脚本不会被误提交。
- **shell 会 mangle 带斜杠的参数**：`git branch feat/x` 会静默失败并报 `fatal: invalid reference`。
  **可靠做法：用 Python `subprocess.run(['git', ...])` 调 git**（这一轮全程这么做，稳定），
  分支名用连字符。
- **Bash 工具的双引号里不要出现反引号**：bash 会当命令替换执行、静默吃掉内容。
  复杂脚本一律用 Write 落盘再跑。
- Python 用 `.venv/Scripts/python.exe`（该 venv 由 uv 建，原本没有 pip）。
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

## 工作区与合并（2026-09-14）

| 位置 | 分支 | 负责 | 内容 |
|---|---|---|---|
| `MediaCrawler-main/` | `master` | 主工作区 | 收藏、托盘与扫码登录在此集成；日常运行统一使用 dist |
| `MediaCrawler-side-tasks/`（git worktree） | `side-tasks` | 支线任务工作区 | 独立目录、独立端口（8090）；**第一个支线任务是「应用自带扫码登录」，已合入 master**；下一个支线从这里开分支 |

两个目录**共用一个 `.git`**（`MediaCrawler-main/.git`），可以分别提交，不需要 push/pull。
并行开发用 `SIYE_PORT` 显式分配端口；产品默认 8080，扩展目前只支持该端口。

⚠️ **共用 `.git` 的三条注意事项**（踩过）：

1. **切目录 ≠ 切分支**：每个 worktree 有自己的 HEAD，在一边 `checkout` 不会影响另一边 ——
   但**同一个分支不能在两个 worktree 同时检出**。要动 master 就去主目录动。
2. **一边的操作会改共用元数据**：例如 `git worktree prune`、`git gc`、改 `.git/config`
   影响的是两个目录。**在该目录存在未提交改动时，别在任何一个目录里跑 prune / reset --hard。**
3. **`git worktree list` 里的记录目录名可能是旧的**（当前记录仍叫 `MediaCrawler-scanlogin`，
   是本 worktree 旧名）。那只影响内部命名，**不要手动删它**：
   删了就丢掉 worktree 身份。目录改名后若看到 `prunable`，用
   `git worktree repair "<新目录路径>"` 修（它只重写一行 `gitdir` 指针）。

### 支线任务 worktree 怎么启动

`MediaCrawler.bat` 的**产品行为不变**：存在 `dist\SiYe\四野.exe` 就直接启动它。
（原来还有一个内容与它逐字节相同的 `启动.bat`，2026-09-14 已删 —— 双击 `MediaCrawler.bat` 即可。）
**没有打包产物时**（开发用 worktree 通常如此）回退到 `启动-源码.bat` —— 从源码起后端，
**端口默认 8090**（本目录专用，避免和主目录的 8080 撞车），可用 `SIYE_PORT` 覆盖。

- 端口只有 `base/server_port.py` 一处解析（`api/main.py`、`desktop_main.py`、`tray_main.py`、
  `scripts/start.ps1 -Port`、vite dev 代理都读它）。
- 浏览器扩展仍固定 8080，所以换端口的实例请用内置扫码登录，不要用扩展同步。
- 想临时换端口：`set SIYE_PORT=8123` 后再双击启动。
