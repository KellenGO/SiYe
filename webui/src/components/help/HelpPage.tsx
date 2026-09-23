import { useEffect, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { SupportDiagnostics } from './SupportDiagnostics'

const ORIGINAL_PROJECT_URL = 'https://github.com/NanmiCoder/MediaCrawler'
const CURRENT_PROJECT_URL = 'https://github.com/KellenGO/SiYe'

interface HelpPageProps {
  onShowDisclaimer: () => void
  onStartGuide: () => void
}

export function HelpPage({ onShowDisclaimer, onStartGuide }: HelpPageProps) {
  const { t } = useTranslation()
  // 版本号以后端报告为准（前后端版本不匹配时用户能立刻看出来）。
  const [apiVersion, setApiVersion] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    fetch('/api/health')
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (!cancelled && payload && typeof payload.version === 'string') setApiVersion(payload.version)
      })
      .catch(() => { /* 取不到就不显示，帮助页其余内容照常可用 */ })
    return () => { cancelled = true }
  }, [])
  useEffect(() => {
    const focusAdvancedHelp = () => {
      if (window.location.hash !== '#/help/advanced-reset-sync') return
      const section = document.getElementById('advanced-reset-sync')
      section?.scrollIntoView({ block: 'start' })
      section?.focus({ preventScroll: true })
    }
    window.addEventListener('hashchange', focusAdvancedHelp)
    focusAdvancedHelp()
    return () => window.removeEventListener('hashchange', focusAdvancedHelp)
  }, [])
  return (
    <div className="preview-container help-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">HELP &amp; ABOUT</p>
          <h1>从第一次搜索开始</h1>
          <p className="description">先连接常用的平台，完成一次搜索，再慢慢熟悉四野。</p>
        </div>
      </div>

      <div className="help-grid">
        <section className="help-section">
          <h2>使用流程</h2>
          <p>{t("onboarding.helpIntro")}</p>
          <div className="button-row"><button type="button" className="btn" onClick={onStartGuide}>{t("onboarding.restart")}</button></div>
          <ol className="steps">
            <li><strong>打开四野</strong>保持应用运行，页面会自动检查连接状态。</li>
            <li><strong>登录平台账号</strong>到「设置 · 账号与登录」，点任意平台的「扫码登录」，用手机 App 扫一下即可。</li>
            <li><strong>开始聚合搜索</strong>输入关键词、选择平台，已返回的内容可以边搜边看。</li>
            <li><strong>收藏与整理</strong>保存到本地收藏，添加备注，或导出一份备份。</li>
          </ol>
        </section>

        <section className="help-section">
          <h2>扫码登录（推荐）</h2>
          <ol>
            <li>打开「设置 · 账号与登录」，找到要登录的平台卡片。</li>
            <li>点卡片上的<strong>「扫码登录」</strong>；四野会在你电脑上打开一个浏览器窗口。</li>
            <li>用对应平台的手机 App 扫码，登录成功后窗口会自动关闭。</li>
            <li>四野会立刻验证这次会话，卡片状态变成「登录已确认」即完成。先连接你常用的 1–2 个平台，搜索时勾选它们即可。</li>
          </ol>
        </section>

        <section className="help-section">
          <h2>从浏览器同步登录（可选）</h2>
          <p>不安装扩展也可以用四野内置扫码登录。</p>
          <details className="help-extension-details mt-3">
            <summary>已经在 Chrome / Edge 登录？展开查看免扫码同步方法（可选）</summary>
            <p>如果你已经在 Chrome / Edge 里登录过这些平台，装一次扩展就能把登录状态同步到这台电脑上的四野。</p>
            <ol>
              <li>打开 <code>chrome://extensions</code>，Edge 使用 <code>edge://extensions</code>。</li>
              <li>开启“开发者模式”，点击“加载已解压的扩展程序”。</li>
              <li>安装版选择 <code>%LOCALAPPDATA%\Programs\SiYe\browser_extension</code>；便携版选择解压目录里的 <code>SiYe/browser_extension/</code>。</li>
              <li>刷新四野页面，然后到「设置 · 账号与登录」点「从浏览器同步」。</li>
            </ol>
            <p className="mt-3">扩展只用于把你浏览器里已有的登录状态交给本机四野，不读取其他数据，也不上传到外部服务器。两种登录方式可以混用。</p>
          </details>
        </section>

        <section className="help-section">
          <h2>两种收藏</h2>
          <p><strong>本地收藏</strong>保存你从搜索结果中选中的内容，可写备注、筛选和备份。</p>
          <p className="mt-3"><strong>跨平台收藏</strong>从已登录平台读取收藏列表，并允许再保存到本地。同步不会添加、删除或移动平台中的收藏。</p>
        </section>

        <section className="help-section wide" id="pages">
          <h2>页面与功能一览</h2>
          <p>按页面来查：每个页面能做什么，都在这里。新手引导只带你走一遍，细节以这一节为准。</p>
          <ol className="steps">
            <li><strong>首页</strong>搜索入口。搜索框下方的平台按钮是勾选框，勾了才参与这次搜索，默认一个都不勾、由你自己选；旁边是最近搜索和热搜，点一条热搜词直接用当前勾选的平台搜一次。顶栏的「自定义」可切成极简模式。</li>
            <li><strong>搜索结果</strong>每个平台一个状态块，失败的会说明原因并可以单独重试；上方可切「综合 / 最新 / 互动最多」排序，排序旁的输入框只在当前结果里按关键词筛选。结果不够时点列表底部的「继续搜索」再多取一批。</li>
            <li><strong>结果卡片</strong>点标题打开原文；书签收藏到本地；钟表加入「稍后再看」；也可以给单条内容写备注。这些操作都不会改动平台里的原始收藏。</li>
            <li><strong>本地收藏</strong>「全部」「默认收藏夹」「稍后再看」和自建收藏夹；可选列表或图标两种浏览方式；支持批量管理、写备注；「备份管理」可导出备份。</li>
            <li><strong>跨平台收藏</strong>查看你在各平台收藏过的内容，也可以挑内容另存到本地。「同步所选平台 / 继续」只新增；高级操作中的「重置同步」覆盖所选且成功读取平台的本机跨平台收藏。两者都不会改动平台里的收藏。</li>
            <li><strong>观看历史</strong>自动记录你点开看过的内容，只保存在这台电脑上，可以随时删除。</li>
            <li><strong>设置 · 搜索设置</strong>每个平台每次搜索取多少条（默认 20 条）。条数越大越慢，也更容易遇到平台限制。</li>
            <li><strong>设置 · 账号与登录</strong>扫码登录是主路径；「从浏览器同步」需要浏览器扩展，是可选加速；也可以在这里验证登录状态或移除登录。</li>
            <li><strong>设置 · 外观与首页</strong>8 种主题色，浅色与深色各自记忆；首页模式可选「极简」或「实用」；最近搜索与热搜可分别开关。</li>
            <li><strong>顶栏</strong>左边是首页 / 收藏 / 历史 / 设置；右边显示本地服务与登录状态（点开可看各平台上次验证时间）、深浅色切换和帮助入口。</li>
          </ol>
        </section>

        <section className="help-section wide" id="advanced-reset-sync" tabIndex={-1}>
          <h2>高级功能说明</h2>
          <h3>重置同步</h3>
          <p><strong>同步所选平台 / 继续：跟进平台收藏夹内容，只新增。</strong>日常使用这个按钮，已存的跨平台收藏会保留。</p>
          <p className="mt-3"><strong>重置同步：直接覆盖本地跨平台收藏。</strong>本机记录与平台列表明显不一致，或想重新建立所选平台的本机记录时使用。先选择平台，展开「高级操作」，点击「重置同步」并确认。取消确认不会改数据，开始后仍可取消。</p>
          <p className="mt-3">只有所选且成功完整读取的平台会被覆盖。未选、读取失败或未登录平台的旧记录会保留；个人「本地收藏」也不会删除。两种操作都不会在平台上添加、删除或移动收藏。</p>
        </section>

        <SupportDiagnostics />

        <section className="help-section">
          <h2>项目与作者</h2>
          <p>当前界面与功能由 KellenGong 维护，基础采集能力来自 MediaCrawler 原项目。</p>
          {apiVersion && <p className="mt-2">当前版本 v{apiVersion}</p>}
          <div className="button-row">
            <a className="text-link" href={CURRENT_PROJECT_URL} target="_blank" rel="noreferrer">当前项目 <ExternalLink /></a>
            <a className="text-link" href={ORIGINAL_PROJECT_URL} target="_blank" rel="noreferrer">原项目 <ExternalLink /></a>
          </div>
        </section>

        <section className="help-section wide" id="disclaimer">
          <h2>使用须知</h2>
          <p>请在法律与平台规则允许的范围内使用。本项目仅用于学习和研究；账号、收藏与搜索数据均由本机服务处理。使用前请阅读完整免责声明。</p>
          <div className="button-row"><button className="btn" type="button" onClick={onShowDisclaimer}>查看完整使用须知</button></div>
        </section>
      </div>
    </div>
  )
}
