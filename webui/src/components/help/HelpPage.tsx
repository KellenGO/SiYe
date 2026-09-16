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
            <li><strong>启动本机服务</strong>保持四野后端运行，网页会自动检查连接状态。</li>
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
          <h2>用浏览器扩展同步（可选）</h2>
          <p>如果你已经在 Chrome / Edge 里登录过这些平台，装一次扩展就能把登录状态直接同步过来。</p>
          <ol>
            <li>打开 <code>chrome://extensions</code>，Edge 使用 <code>edge://extensions</code>。</li>
            <li>开启“开发者模式”，点击“加载已解压的扩展程序”。</li>
            <li>选择项目中的 <code>browser_extension</code> 文件夹。</li>
            <li>刷新四野页面，然后到「设置 · 账号与登录」点「从浏览器同步」。</li>
          </ol>
          <p className="mt-3">这是可选加速方式：不装扩展也能用扫码登录，两者可以混用。</p>
        </section>

        <section className="help-section">
          <h2>两种收藏</h2>
          <p><strong>本地收藏</strong>保存你从搜索结果中选中的内容，可写备注、筛选和备份。</p>
          <p className="mt-3"><strong>跨平台收藏</strong>从已登录平台读取收藏列表，并允许再保存到本地。同步不会添加、删除或移动平台中的收藏。</p>
        </section>

        <SupportDiagnostics />

        <section className="help-section">
          <h2>项目与作者</h2>
          <p>当前界面与功能由 KellenGong 维护，基础采集能力来自 MediaCrawler 原项目。</p>
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
