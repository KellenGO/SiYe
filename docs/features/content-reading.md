# 知乎站内阅读

## 一句话

知乎回答和文章可以直接在四野的详情面板阅读正文与图片，沿用当前主题，并保留原文入口。

## 代码入口

| 职责 | 位置 |
|---|---|
| 本机阅读接口、关闭请求取消 | `api/routers/reading.py` 的 `read_detail` |
| 会话复用、获取与短时缓存 | `api/services/reading.py` 的 `ReadingService`、`fetch_reading` |
| 正文分块、受限状态与图片来源检查 | `api/services/reading_content.py` 的 `reading_detail`、`detail_from_page` |
| 阅读界面、字号与观看历史 | `webui/src/components/reading/ReadingBody.tsx` |
| 支持范围与响应检查 | `webui/src/lib/reading.ts` |
| 共同详情与列表入口 | `webui/src/components/favorites/LocalContentDrawer.tsx`、`webui/src/components/search/ResultCard.tsx` |
| 主题与窄屏阅读样式 | `webui/src/index.css` |

## 关键决定

- 参照 [OpenScope](https://github.com/metaMMY07/OpenScope) 的「平台取数与自身展示分离」思路，在现有 React 详情面板增加阅读能力，不引入 Flutter、播放器框架或新的依赖。取舍见[站内阅读从知乎开始](../decisions/2026-10-10-站内阅读从知乎开始.md)。
- 可信且身份匹配的知乎回答／文章在列表中点击标题或封面进入阅读；网格沿用详情入口。搜索、收藏、历史及空间资料共用同一详情面板。其它内容保持已有信息展示与原文入口。
- 仅在打开详情时获取正文，不在搜索列表批量预抓。复用 Accounts 的知乎登录态与现有 HTTP 签名客户端；接口拒绝或结构异常时只进行一次已有登录浏览器补取，按当前内容 ID 从页面状态提取，不取实体列表中的第一篇文章。
- 开始取数前检查后台搜索仍在运行的情况，并停止知乎空闲 worker；整个获取过程持有操作租约，避免账号操作、搜索和浏览器 profile 互相争用。正在搜索或处理账号时提供重试提示；缓存命中不访问平台。
- 只传递段落、标题、引用、代码、列表和可信知乎 CDN 图片。原始 HTML、网页脚本、Cookie、签名和页面状态不返回前端；不把搜索摘要当作正文。付费、隐藏、截断及不支持的嵌入内容明确提示，并保留「在原平台打开」。
- 正文默认 18px，可在本次详情调整到 16–24px；刷新失败保留已加载正文和字号。正文成功显示后记一次当前来源的观看历史，刷新不重复记录；读取失败、空的受限正文或关闭尚未完成的请求不记观看。
- 成功结果只在内存中短暂复用，按账号代数隔离，最多六项、每项两分钟；不写全文存档，不修改收藏／空间快照。关闭详情取消前端请求，后端检测断开后取消取数并释放会话及租约。

## 已知坑 / 边界

- 本次范围是知乎回答和文章；评论、视频、图集缩放与其它平台阅读尚未接入。标题、作者、时间和指标沿用现有内容快照，正文取得后不回写这些资料。
- 站内排版保留文字及图文顺序，行内加粗、链接和编号列表等暂简化为文字／列表项；表格、复杂公式和嵌入媒体提示去原文查看，不承诺复现完整官方页面。
- 登录、验证、平台写操作和受限内容继续在官方页面处理；不会自动过验证码、绕过权限或无限重试。长正文有明确的保留上限和截断提示。
- 图片直接读取可信知乎 CDN，不带平台 Cookie；CDN 拒绝、图片过期或网络失败时显示占位，可打开原文查看。未新增任意 URL 代理。
- 构建、隔离平台测试及桌面／窄屏界面验证不能证明真实知乎账号一直可用；真实账号正文、图片与风控边界仍需实际使用验收。现有 EXE 尚未为此重建，源码构建与旧安装包能力不同。

## 测试怎么跑

- 后端：`tests/test_reading.py`，使用 `.venv/Scripts/python.exe -m pytest tests/test_reading.py --basetemp=.tmp_pytest_reader_<唯一编号>`；覆盖身份、来源、受限／空正文、取数与浏览器关闭、缓存、取消、搜索互斥、本机接口及异常隐私。
- 前端：在 `webui/` 运行 `npm run test:search` 和 `npm run build`；`webui/tests/reading.test.ts` 检查支持范围、图片与响应身份和安全错误文案。
- 界面：构建后运行 `.venv/Scripts/python.exe scripts/reading_ui_smoke.py`，使用临时 SQLite、模拟平台与独立 Edge，检查列表／网格入口、正文和图片、失败重试、刷新保留、历史、取消、320px／390px 和深浅主题；截图保存在 `build/reading-ui/`。
