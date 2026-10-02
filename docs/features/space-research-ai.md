# 空间 AI 研究助手

## 一句话

围绕用户选入空间的跨平台资料，获取正文、评论与平台字幕，再通过内置 Claude Agent SDK 生成带来源的笔记；先预览，由用户追加到空间总笔记。联网补充由每个空间单独选择，默认关闭。

## 代码入口

| 职责 | 位置 |
|---|---|
| 本机 AI 配置、密钥加密与空间偏好 | `api/services/research_config.py`（`ResearchConfig`） |
| 获取空间内的平台正文、评论与字幕 | `api/services/research_materials.py`（`MaterialCollector`） |
| SDK 工具权限与模型分析 | `api/services/research_agent.py`（`run_agent`） |
| 分段读取、覆盖记录与笔记转换 | `api/services/research_documents.py`（`MaterialAccess`、`result_document`） |
| 公开网页读取与跳转检查 | `api/services/research_web.py`（`public_target`、`public_get`） |
| 任务状态、隔离进程、取消与超时 | `api/services/research_jobs.py`（`ResearchJobs`）、`api/services/research_worker.py` |
| 研究接口 | `api/routers/research.py`（`research_router`） |
| 设置、缺口确认、预览与追加 | `webui/src/components/spaces/SpaceResearchPanel.tsx`、`webui/src/lib/researchApi.ts` |
| 笔记追加、冲突与自动保存 | `webui/src/lib/spaceNotes.ts`（`NoteSession.appendResearch`）、`webui/src/components/spaces/SpaceNoteController.tsx` |
| 内置运行程序准备与打包 | `scripts/prepare_agent_runtime.py`、`MediaCrawler.spec`、`scripts/build_exe.ps1` |

## 关键决定

- 在现有笔记栏展开「AI 研究助手」。用户填写 Anthropic 兼容服务地址、API Key 和模型名称；留空 Key 保留原配置，可单独清除配置。密钥使用 Windows 当前用户加密，公开接口只返回是否已保存和遮罩。使用 API 计费，不继承本机 Claude 订阅登录。连接测试也可能计费。
- 点击生成先固定空间资料快照、研究问题和联网选择，再获取资料。新增或移出的来源只影响下一任务，当前结果会提示快照已变化。抓取不会修改空间原快照、收藏或用户笔记。
- 标题和简介取自已加入的快照，补取正文、最多 50 条评论（含少量回复）及可访问的平台字幕。B站与知乎请求热门评论，小红书、抖音保留平台默认顺序；实际顺序、截断与失败都展示。后续分页失败保留已有内容，重试成功项保持不变。没有字幕时明确说明，不使用音视频识别。
- 资料获取后等待用户检查，允许重试失败项或使用已取得内容继续分析。等待确认不占处理时间。长资料按段开放给 SDK，记录实际读取的分段数，未完整读取的资料写进预览；模型不会得到其他空间的资料、数据库、登录 cookie 或 API Key 文本。
- 全局只运行一个研究任务，包括等待资料确认。平台获取阶段与搜索、收藏同步、账号操作互斥；分析阶段释放平台资源。任务累计实际处理最多十分钟，取消、归档、删除和应用退出会停止相关进程；Windows 同时关闭全部子进程并清理临时会话。
- 联网偏好按空间保存，默认关闭，修改只影响下次任务。关闭时只暴露选定空间的读取工具；开启时额外开放框架搜索和四野受控的网页读取。网页读取逐次检查域名解析与跳转，并固定公开地址连接。文件、命令、浏览器控制与其他 MCP 不开放。搜索摘要和已读取正文分别标注，部分正文已读也明确记录；外部结论与空间结论分节显示。
- 结果先显示预览，追加时再次检查空间及笔记版本，插入当前编辑器末尾成为一次可撤销操作，保留生成期间的手写内容。保存冲突、输入法组词和超限时拒绝追加并保留预览；同一浏览器会话刷新后也不能重复追加同一任务。生成不会自动覆盖正文。
- 内置 SDK 与固定版本官方 Windows 运行程序，构建时校验下载完整性并保留上游说明和 SDK 许可。普通安装包用户无需另外安装 Claude Code。长期取舍见[内置研究 SDK 与可选联网](../decisions/2026-10-02-空间AI内置SDK与可选联网.md)。

## 已知坑 / 边界

- 「网页公开」不保证平台接口允许匿名读取。小红书、知乎的签名依赖本机会话；抖音使用应用自己的浏览器会话。访问受限显示缺口，框架不能绕过平台限制。B站匿名正文、评论和字幕也可能受登录、风控或字幕权限限制。
- 自定义服务必须兼容 Anthropic Messages、工具循环和结构化输出。仅支持 OpenAI Chat Completions 的服务不能直接使用；框架搜索还取决于服务是否支持该能力。连接测试在当前联网模式下实测工具循环；仅运行程序就绪不等于服务可用。
- 当前适配的字幕入口主要覆盖 B站；小红书与抖音只读取详情中实际返回的字幕地址，没有字幕地址时显示缺失。未实现声音转写、画面识别、OCR、媒体下载或登录网页自动浏览。外部网页只读公开文字，不能保证动态页面、付费内容或登录内容可读。
- 评论不是全量，回复只取少量，资料片段不是事实保证。模型可能遗漏来源或产生错误推断；覆盖记录说明读取情况，不能保证语义上完全理解。
- 任务、抓取正文和未追加的预览只保留在应用进程内，应用重启后需重新生成。已追加的笔记和空间联网偏好保留。生成不会提前把空间笔记正文发送给模型。
- 普通用户端支持 Windows 当前用户密钥加密。复制配置到其他账号或机器后需重新填写 Key。模型报告费用可能与代理实际账单不同，显示值供参考。
- 单次网页最多 2 MB、最多五次跳转；字幕 JSON 同样限制响应大小。研究进程通信有 16 MB 单条消息上限，超大资料集合可能失败，需拆成更小空间；不会静默截去已选来源冒充全量分析。
- 当前有模拟平台响应、真实 SDK 接本机模拟服务及 Windows 构建验收；尚未用真实付费服务或真人平台资料逐一验收，平台接口与第三方服务兼容性需要实际使用观察。

## 测试怎么跑

- 后端：`.venv/Scripts/python.exe -m pytest tests/test_research.py tests/test_spaces.py tests/test_account_coordinator.py tests/test_tray_launcher.py --basetemp=.tmp_pytest_ai/check`，先创建父目录；同时运行文档与 UI 契约检查。
- 前端：`webui` 内运行 `npm run test:search` 和 `npm run build`，笔记测试覆盖生成期间修改、重复追加、冲突与超限。
- SDK：先按项目锁文件安装依赖并运行 `scripts/prepare_agent_runtime.py`，再运行 `.venv/Scripts/python.exe scripts/research_ai_smoke.py`。使用真实运行程序连接本机模拟 Anthropic 服务，不用真实 Key、不产生服务费用；覆盖工具循环、结构化笔记、工具范围、进程树取消和临时目录清理。
- Windows 包：上述 SDK smoke 加 `--exe build/ai-exe-dist/SiYe/SiYe.exe`，验证冻结后的研究 worker。构建仍由 `scripts/build_exe.ps1` 统一入口准备运行程序。
- 界面：构建后运行 `scripts/research_ui_smoke.py` 与 `scripts/spaces_ui_smoke.py`。临时 SQLite 与模拟研究服务，覆盖缺口确认、任务联网快照、生成期间手写、预览追加、刷新防重复、空间独立偏好和窄屏；截图在忽略的 `build/`。
