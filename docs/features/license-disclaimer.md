# 许可与免责声明（首次启动的接受门）

## 一句话

第一次打开界面前必须确认「仅供学习研究、不得商用」的免责声明；点「同意」后记在本机
localStorage，之后不再打扰；点「不同意」会尝试关闭当前标签页。

## 代码入口

| 职责 | 位置 |
|---|---|
| 声明弹层与接受逻辑 | `webui/src/components/license/LicenseDisclaimer.tsx`（`LicenseDisclaimer`、`isLicenseAccepted`） |
| 接受状态的门控（未接受时其余界面整块不渲染） | `webui/src/App.tsx` 的 `licenseAccepted` 状态与 `handleLicenseAccept` |
| 文案（中英） | `webui/src/i18n/locales/{zh-CN,en-US}/license.json` |
| 法律条款正文 | 仓库根 `LICENSE`（NON-COMMERCIAL LEARNING LICENSE 1.1） |
| 落地页对应区块 | `site/index.html` 的许可小节（`#license`）、`site/guide.html` 的「能商用吗」 |

## 关键决定

- **接受状态存在 localStorage**（key `mediacrawler_license_accepted`，值为字符串 `"true"`），
  不上报、不落库。清浏览器数据会重新弹一次 —— 这是可接受的，因为它是"同意"而不是"数据"。
- **未接受时整个界面不渲染**（`App.tsx` 里身份校验、自动同步、主界面都带
  `licenseAccepted && !showDisclaimer` 条件），而不是只遮一层蒙版 ——
  避免用键盘/脚本绕过蒙版后仍能操作。
- 「不同意」走 `window.close()` 尽力而为：浏览器通常只允许关闭脚本自己打开的标签，
  关不掉时保持弹层不变，不给绕行入口。
- 文案与图形按 i18n 走（`license` 命名空间），是本项目**少数真正接入 i18n 的界面之一**。

## 已知坑 / 边界

- 这个接受状态**只是本地标记，不是许可本身**。它不构成任何法律效力，也不阻止别人
  直接调 API。真要收紧得在后端做，目前刻意不做（本工具只监听 `127.0.0.1`，
  且只给同一个人用）。
- `webui/src/App.tsx` 里 `showDisclaimer` 允许再次弹出声明（用于「关于/许可」入口），
  它与 `licenseAccepted` 是两个独立条件，改其中一个时注意别把另一个短路掉。
- 本文件长期是**零文档状态**（wiki 覆盖率缺口里风险最高的一项，因为它涉及合规），
  2026-09-14 补上。修改免责文案时请同时改 `LICENSE`、`site/` 落地页与两个语言的
  `license.json` —— 四处口径必须一致。

- 拒绝路径改成就地渲染（2026-10-21）：原来在 200ms 定时器里覆盖 `document.body.innerHTML`，会把 React 根节点一起抹掉（返回 / 回退后整页不可用），而且 100ms 的 `about:blank` 跳转基本让它跑不到。现在 `window.close()` 被浏览器拦下时就地渲染拒绝页，文案走 `license.json`。


## 测试怎么跑

暂无专门用例（前端组件零渲染测试）。相关间接覆盖：`tests/test_webui_ui_contract.py`
会对前端源码做静态断言；文案改动后建议人工打开页面确认弹层行为。
