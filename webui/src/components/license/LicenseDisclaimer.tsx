import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ShieldAlert, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'

const LICENSE_KEY = 'mediacrawler_license_accepted'

// 检查是否已经接受协议
export function isLicenseAccepted(): boolean {
  return localStorage.getItem(LICENSE_KEY) === 'true'
}

interface LicenseDisclaimerProps {
  onAccept: () => void
}

export function LicenseDisclaimer({ onAccept }: LicenseDisclaimerProps) {
  const { t } = useTranslation('license')
  const [declined, setDeclined] = useState(false)

  const handleConfirm = () => {
    localStorage.setItem(LICENSE_KEY, 'true')
    onAccept()
  }

  const handleDecline = () => {
    // 脚本打开的窗口才能被 close() 关掉；浏览器拦下时就地渲染拒绝页。
    // 以前这里是 setTimeout 里覆盖 document.body.innerHTML —— 那会把 React
    // 根节点一起抹掉，返回/回退后整页不可用；而且 100ms 的 about:blank 跳转
    // 会让 200ms 的渲染基本跑不到。
    try {
      window.close()
    } catch {
      // 被安全策略拦下是正常情况，继续显示拒绝页。
    }
    setDeclined(true)
  }

  if (declined) {
    return (
      <div className="fixed inset-0 z-[100] grid place-items-center bg-[#0d1117] p-6 text-center text-[#f85149]">
        <div>
          <div className="mb-5 text-5xl" aria-hidden="true">⛔</div>
          <h2 className="mb-2 text-2xl font-bold">{t('declinedTitle')}</h2>
          <p className="text-sm text-[#8b949e]">{t('declinedHint')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-[#172b3d]/60 backdrop-blur-sm flex items-center justify-center z-[100] overflow-y-auto py-8 px-4">
      <div className="bg-cyber-bg-secondary border border-cyber-border-default rounded-[18px] shadow-[0_24px_70px_rgba(50,105,145,0.16)] p-6 sm:p-8 max-w-2xl w-full relative">
        {/* Header */}
        <div className="flex items-center justify-center gap-3 mb-4">
          <span className="w-11 h-11 rounded-[13px] rounded-bl-[5px] bg-brand/15 grid place-items-center">
            <ShieldAlert className="w-6 h-6 text-brand-strong" />
          </span>
          <h2 className="text-xl font-bold text-cyber-text-primary">
            {t('title')}
          </h2>
        </div>

        {/* Warning subtitle */}
        <div className="text-center mb-5">
          <span className="text-[13.5px] text-warn font-medium">
            {t('warning')}
          </span>
        </div>

        {/* Content box */}
        <div className="bg-cyber-bg-tertiary border border-cyber-border-subtle rounded-xl p-4 mb-5">
          <ul className="space-y-2.5 text-[13.5px]">
            <li className="flex items-start gap-2.5">
              <span className="text-brand-strong font-bold flex-shrink-0">1.</span>
              <span className="text-cyber-text-primary">{t('content.line1')}</span>
            </li>
            <li className="flex items-start gap-2.5">
              <span className="text-brand-strong font-bold flex-shrink-0">2.</span>
              <span className="text-cyber-text-primary">{t('content.line2')}</span>
            </li>
            <li className="flex items-start gap-2.5">
              <span className="text-brand-strong font-bold flex-shrink-0">3.</span>
              <span className="text-cyber-text-primary">{t('content.line3')}</span>
            </li>
            <li className="flex items-start gap-2.5">
              <span className="text-brand-strong font-bold flex-shrink-0">4.</span>
              <span className="text-cyber-text-primary">{t('content.line4')}</span>
            </li>
          </ul>
        </div>

        {/* License Link */}
        <div className="flex justify-center mb-6">
          <a
            href="https://github.com/KellenGO/SiYe/blob/main/LICENSE"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-brand-strong hover:underline text-sm"
          >
            <ExternalLink className="w-4 h-4" />
            {t('license')}
          </a>
        </div>

        {/* Action buttons */}
        <div className="flex gap-3">
          <Button
            onClick={handleDecline}
            variant="outline"
            className="flex-1 border-danger/50 text-danger hover:bg-danger-soft hover:border-danger/50"
          >
            {t('decline')}
          </Button>
          <Button
            onClick={handleConfirm}
            className="flex-1 bg-brand text-white font-bold hover:bg-brand-strong"
          >
            {t('confirm')}
          </Button>
        </div>
      </div>
    </div>
  )
}
