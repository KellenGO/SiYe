# 空间 AI 研究助手

## 一句话

围绕用户选入空间的跨平台资料，获取正文、评论与平台字幕，再通过默认的 OpenAI 兼容 API（支持 DeepSeek、Gemini 等工具调用模型）生成带来源的笔记；先预览，由用户追加到空间总笔记。联网补充由每个空间单独选择，默认关闭。

## 代码入口

| 职责 | 位置 |
|---|---|
| 本机 AI 配置、密钥加密与空间偏好 | `api/services/research_config.py`（`ResearchConfig`） |
| 获取空间内的平台正文、评论与字幕 | `api/services/research_materials.py`（`MaterialCollector`） |
| 协议路由、旧 SDK 兼容与模型分析 | `api/services/research_agent.py`（`run_agent`） |
| OpenAI 兼容模型的受控工具循环 | `api/services/research_openai.py`（`run_openai`、`completion`） |
| 分段读取、覆盖记录与笔记转换 | `api/services/research_documents.py`（`MaterialAccess`、`result_document`） |
| 公开网页读取与跳转检查 | `api/services/research_web.py`（`public_target`、`public_get`、`search_public`） |
| 任务状态、隔离进程、取消与超时 | `api/services/research_jobs.py`（`ResearchJobs`）、`api/services/research_worker.py` |
| 研究接口 | `api/routers/research.py`（`research_router`） |
| 全局 AI 服务设置 | `webui/src/components/accounts/ResearchSettings.tsx`、`webui/src/components/accounts/AccountsPage.tsx` |
| 右侧助手、会话、缺口确认与追加 | `webui/src/components/spaces/SpaceWorkspace.tsx`、`webui/src/components/spaces/SpaceResearchPanel.tsx`、`webui/src/lib/researchApi.ts` |
| 笔记追加、冲突与自动保存 | `webui/src/lib/spaceNotes.ts`（`NoteSession.appendResearch`）、`webui/src/components/spaces/SpaceNoteController.tsx` |
| 内置运行程序准备与打包 | `scripts/prepare_agent_runtime.py`、`MediaCrawler.spec`、`scripts/build_exe.ps1` |

## 关键决定

- 参照 Claudian 的设置与会话分工，助手通过页面右上角独立入口展开，不再放在笔记栏内。桌面固定在右侧并压缩左侧内容，可同时编辑笔记；宽度不足时改成全屏原生模态面板并限制焦点。Escape 只关闭助手，关闭不取消研究，重新打开继续读取进度。以聊天消息为主：问题在右侧气泡、回答顺序展开、读取记录默认折叠，确认资料操作放在对应消息内，底部固定输入、模型、联网及发送／停止；会话列表支持标题搜索，每轮回答都可追加到笔记。没有移植 Obsidian 工作区 API 或引入新的前端框架。
- AI 服务配置位于全局「设置 → AI 服务配置」，所有空间共用。默认使用 OpenAI Chat Completions 协议，提供 DeepSeek、Gemini、OpenAI 及自定义服务选项，模型名称可自行填写。填写服务基础地址、API Key 和模型名称；同一服务留空 Key 保留原配置，切换服务商需要填写对应 Key，可单独清除配置。旧配置未声明协议时仍按 Anthropic 执行，不静默更换地址或 Key。「保存并测试连接」先保存当前输入，再用设置页的测试联网选项验证；该选项不改变空间研究偏好；运行或等待确认时不能更改服务配置。密钥使用 Windows 当前用户加密，公开接口只返回是否已保存和遮罩。本机接口严格检查 Host 和 Origin，拒绝恶意域名自称同源。使用 API 计费，默认模式无需 Claude 账号或运行程序，不继承本机 Claude 订阅登录。连接测试也可能计费。
- 点击生成先固定空间资料快照、研究问题和联网选择，再获取资料。新增或移出的来源只影响新会话，当前结果会提示快照已变化。抓取不会修改空间原快照、收藏或用户笔记。
- 可以开新会话、切换当前空间的历史会话和连续追问。首次研究先获取并确认资料；同一会话的追问复用原资料和快照，携带最近三轮已完成的问答（每轮答案最多 8000 字），仍要求重新读取资料并验证引用。历史仅帮助理解问题，不替代当前来源依据；新的联网选择只用于下一轮。任务未完成或等待确认时，需要先完成或取消才能切换或新建，避免丢失正在处理的任务。
- 标题和简介取自已加入的快照，补取正文、最多 50 条评论（含少量回复）及可访问的平台字幕。B站与知乎请求热门评论，小红书、抖音保留平台默认顺序；实际顺序、截断与失败都展示。后续分页失败保留已有内容，重试成功项保持不变。没有字幕时明确说明，不使用音视频识别。
- 资料获取后等待用户检查，允许重试失败项或使用已取得内容继续分析。等待确认不占处理时间。重试中账号或客户端初始化失败也保留之前取得的部分评论、字幕。长资料按段开放给受控工具，记录实际读取的分段数；完全未读取的空间来源不能成为引用，部分读取仍标明未完整分析。这只能验证调用记录，不能保证模型理解或结论正确。模型不会得到其他空间的资料、数据库、登录 cookie 或 API Key 文本。
- 全局只运行一个研究任务，包括等待资料确认。平台获取阶段与搜索、收藏同步、账号操作互斥；分析阶段释放平台资源。任务累计实际处理最多十分钟，取消、归档、删除和应用退出会停止相关进程；Windows 同时关闭全部子进程并清理临时会话。
- 联网偏好按空间保存，默认关闭，修改只影响下次任务。关闭时只暴露选定空间的读取工具；开启时默认开放四野提供的公开搜索和受控网页读取，不要求模型服务自带搜索。公开搜索读取 DuckDuckGo HTML 结果，只提供少量摘要，不能把摘要当作正文；旧 Anthropic 模式仍用 SDK 搜索。网页读取逐次检查域名解析与跳转，并固定公开地址连接。文件、命令、浏览器控制与其他 MCP 不开放。搜索摘要和已读取正文分别标注，部分正文已读也明确记录；外部结论与空间结论分节显示。
- 结果先显示预览，追加时再次检查空间及笔记版本，插入当前编辑器末尾成为一次可撤销操作，保留生成期间的手写内容。保存冲突、输入法组词和超限时拒绝追加并保留预览；保存失败明确显示已插入草稿但未保存，重试只保存、不重复插入。防重复标记在服务端确认保存后写入，刷新后仍阻止重复；保存失败后刷新可重新追加。生成不会自动覆盖正文。
- 聊天内同时展示最近十轮，避免长期连续追问堆积界面；状态轮询只返回资料状态、数量、原因和最多四十条可信操作标签，不展示模型内部推理、工具原始参数或认证内容，不反复传输正文、评论、字幕或简介。完成任务按最近顺序保留，最多十个且按约 32 MB 的序列化数据预算淘汰；至少保留最新的一个，正在运行或等待确认的任务不淘汰。
- 默认工具循环复用现有 HTTP 客户端，不新增模型 SDK 或框架。只开放空间清单、分段读取、受控联网和结构化结果提交；DeepSeek 推理内容及 Gemini 工具签名只在当前工具循环中原样回传，不展示或写日志。保留旧 Anthropic SDK 接入和打包能力。长期取舍见[默认 OpenAI 兼容协议](../decisions/2026-10-02-空间AI默认OpenAI兼容协议.md)。

## 已知坑 / 边界

- 「网页公开」不保证平台接口允许匿名读取。小红书、知乎的签名依赖本机会话；抖音使用应用自己的浏览器会话。访问受限显示缺口，框架不能绕过平台限制。B站匿名正文、评论和字幕也可能受登录、风控或字幕权限限制。
- 默认服务必须支持 OpenAI Chat Completions 的工具调用；只支持普通文字生成的模型不能直接用于研究。Gemini 使用官方 OpenAI 兼容基础地址，模型是否可用仍以账号权限为准。旧 Anthropic 模式需要内置运行程序且依赖服务端搜索能力。连接测试实测工具循环，联网选项还实测搜索与网页读取；基础调用成功不等于所有能力可用。
- 公开搜索可能因网络地区、验证码或搜索页变化失败，结果摘要与网页正文分别标记，失败作为缺口保留。模型服务可能限流、余额不足或限制工具参数；不会转为普通文本并冒充已经读取资料。默认协议不支持 OpenAI Responses 或 Gemini 原生 generateContent 地址。
- 当前适配的字幕入口主要覆盖 B站；小红书与抖音只读取详情中实际返回的字幕地址，没有字幕地址时显示缺失。未实现声音转写、画面识别、OCR、媒体下载或登录网页自动浏览。外部网页只读公开文字，不能保证动态页面、付费内容或登录内容可读。
- 评论不是全量，回复只取少量，资料片段不是事实保证。模型可能遗漏来源或产生错误推断；覆盖记录说明读取情况，不能保证语义上完全理解。
- 会话历史、任务、抓取正文和未追加的预览只保留在应用进程内；应用重启或历史任务被淘汰后需重新生成。已保存的笔记和空间联网偏好保留。生成不会提前把空间笔记正文发送给模型。
- 原有 Windows 当前用户密钥加密仍保留。OpenAI 模式报告累计 token，不估算服务商费用；Anthropic 模式沿用模型报告费用。复制配置到其他账号或机器后需重新填写 Key。模型报告费用可能与代理实际账单不同，显示值供参考。
- 单次网页最多 2 MB、最多五次跳转；字幕 JSON 同样限制响应大小。研究进程通信有 16 MB 单条消息上限，超大资料集合可能失败，需拆成更小空间；不会静默截去已选来源冒充全量分析。
- 当前有模拟平台响应、真实 SDK 接本机模拟服务及 Windows 构建验收；尚未用真实付费服务或真人平台资料逐一验收，平台接口与第三方服务兼容性需要实际使用观察。

## 测试怎么跑

- 后端：`.venv/Scripts/python.exe -m pytest tests/test_research.py tests/test_research_openai.py tests/test_spaces.py tests/test_account_coordinator.py tests/test_tray_launcher.py --basetemp=.tmp_pytest_ai/check`，先创建父目录；同时运行文档与 UI 契约检查。
- 前端：`webui` 内运行 `npm run test:search` 和 `npm run build`，笔记测试覆盖生成期间修改、重复追加、冲突与超限。
- SDK：先按项目锁文件安装依赖并运行 `scripts/prepare_agent_runtime.py`，再运行 `.venv/Scripts/python.exe scripts/research_ai_smoke.py`。真实 worker 连接本机模拟 OpenAI / Anthropic 服务，不用真实 Key、不产生服务费用；覆盖工具循环、DeepSeek/Gemini 元数据回传、结构化笔记、工具范围、进程树取消和临时目录清理。
- Windows 包：上述 SDK smoke 加 `--exe build/ai-exe-dist/SiYe/SiYe.exe`，验证冻结后的研究 worker。构建仍由 `scripts/build_exe.ps1` 统一入口准备运行程序。
- 界面：构建后运行 `scripts/research_ui_smoke.py` 与 `scripts/spaces_ui_smoke.py`。临时 SQLite 与模拟研究服务，覆盖当前输入配置的保存与测试、服务商切换、聊天消息与读取记录、缺口确认、任务联网快照、关闭助手后继续手写、保存失败刷新恢复、追加去重、空间独立偏好、桌面右侧压缩布局、新会话／追问／历史切换及手机全屏／固定操作栏／Escape；截图在忽略的 `build/`。
