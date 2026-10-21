import { useTranslation } from 'react-i18next'
import { Github, ShieldCheck } from 'lucide-react'

interface AuthorFooterProps {
  onShowDisclaimer?: () => void
  onNavigateHelp?: () => void
}

/** 原项目 GitHub（NanmiCoder/MediaCrawler）。 */
const ORIGINAL_PROJECT_URL = 'https://github.com/NanmiCoder/MediaCrawler'
/** 当前项目 GitHub（KellenGong 维护/改造的分支）。 */
const CURRENT_PROJECT_URL = 'https://github.com/KellenGO/SiYe'

/**
 * Round 14.3 页脚：普通、低调、随页面内容滚动。
 * 清楚区分原项目（MediaCrawler / 程序员阿江-Relakkes）与当前项目
 * （KellenGong 维护/改造），"KellenGong" 与"当前项目 GitHub"都链接到
 * 当前项目仓库。原项目作者署名保留，不模糊、不误写成官方版本。
 */
export function AuthorFooter({ onShowDisclaimer, onNavigateHelp }: AuthorFooterProps) {
  const { t } = useTranslation()

  return (
    <footer className="mt-8 border-t border-cyber-border-subtle">
      <div className="mx-auto w-full max-w-[1200px] px-4 sm:px-6 py-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 text-[11.5px] text-cyber-text-muted">
        <span className="flex flex-wrap items-center gap-x-1">
          {t('footer.attributionPrefix')}
          <a
            href={CURRENT_PROJECT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-cyber-text-secondary hover:text-brand-strong transition-colors"
          >
            KellenGong
          </a>
        </span>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <button type="button" onClick={onNavigateHelp} className="hover:text-brand-strong transition-colors">{t('footer.help')}</button>
          <span className="hidden sm:inline">{t('footer.localFirst')}</span>
          <a
            href={ORIGINAL_PROJECT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 hover:text-brand-strong transition-colors"
          >
            <Github className="w-3.5 h-3.5" />
            {t('footer.originalProject')}
          </a>
          <a
            href={CURRENT_PROJECT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 hover:text-brand-strong transition-colors"
          >
            <Github className="w-3.5 h-3.5" />
            {t('footer.currentProject')}
          </a>
          <button
            type="button"
            onClick={onShowDisclaimer}
            className="flex items-center gap-1 hover:text-brand-strong transition-colors cursor-pointer"
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            {t('footer.disclaimer')}
          </button>
        </div>
      </div>
    </footer>
  )
}
