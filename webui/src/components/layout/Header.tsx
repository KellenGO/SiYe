import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { ChevronRight } from 'lucide-react'
import { ThemeToggle } from './ThemeToggle'
import { useAccounts } from '@/hooks/useAccounts'
import type { AccountStatusInfo, LoginBadge } from '@/lib/accounts'
import {
  accountSummaryLabel,
  accountTone,
  consumeUnverifiedWarning,
  loginBadgeFrom,
  loginExpiryEvents,
  loginExpiryToastKey,
  markLoginExpiryNotified,
  summarizeAccounts,
  unverifiedWarningCount,
  wasLoginExpiryNotified,
  type AccountTone,
} from '@/lib/accounts'
import { PLATFORM_LABELS, PLATFORM_COLORS } from '@/types/search'
import { PLATFORM_SLUGS } from '@/lib/platformMeta'

const PLATFORM_ORDER = PLATFORM_SLUGS

export type ViewMode = 'search' | 'favorites' | 'history' | 'accounts' | 'help'
export type SettingsSection = 'search' | 'accounts' | 'appearance'

interface HeaderProps {
  viewMode: ViewMode
  onNavigate: (mode: ViewMode, section?: SettingsSection) => void
}


const TONE_DOT: Record<AccountTone, string> = {
  ok: 'bg-[#4f9e79]',
  warn: 'bg-[#d69b50]',
  bad: 'bg-[#c96a6d]',
  idle: 'bg-[#98aaba]',
}

const BADGE_DOT: Record<LoginBadge['kind'], string> = {
  checking: 'bg-[#98aaba]',
  unavailable: 'bg-[#98aaba]',
  summary: 'bg-[#98aaba]',
}

function formatLastVerified(iso: string | null): string | null {
  if (!iso) return null
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return d.toLocaleString('zh-CN')
  } catch {
    return null
  }
}

function AccountPopover({
  accounts,
  onGoAccounts,
}: {
  accounts: AccountStatusInfo[] | null;
  onGoAccounts: () => void;
}) {
  const { t } = useTranslation()
  const summary = accounts ? summarizeAccounts(accounts) : null

  return (
    <div className="absolute right-0 top-[calc(100%+10px)] w-[280px] rounded-[15px] border border-cyber-border-subtle bg-cyber-bg-secondary shadow-[0_10px_30px_rgba(50,105,145,0.12)] p-4 z-30 animate-dsh-drop">
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-[13px] font-semibold text-cyber-text-primary">{t('header.account')}</h3>
        {summary && (
          <span className="text-[11px] text-cyber-text-muted">
            {summary.verified}/{summary.total}
          </span>
        )}
      </div>
      {!accounts && (
        <p className="text-[12px] text-cyber-text-muted py-2">{t('header.accountEmpty')}</p>
      )}
      {accounts && (
        <div>
          {PLATFORM_ORDER.map((p) => {
            const acc = accounts.find((a) => a.platform === p)
            const name = PLATFORM_LABELS[p] || p
            const color = PLATFORM_COLORS[p] || '#4ca4dc'
            const lastVerified = acc ? formatLastVerified(acc.last_verified_at) : null
            if (!acc) {
              return (
                <div key={p} className="flex items-center gap-2.5 py-[7px] text-[12px] text-cyber-text-muted">
                  <span className="w-[7px] h-[7px] rounded-full flex-shrink-0" style={{ backgroundColor: color }} aria-hidden="true">
                  </span>
                  <span>{name}</span>
                  <span className="ml-auto text-[11px]">{t('header.accountEmpty')}</span>
                </div>
              )
            }
            return (
              <div key={p} className="flex items-center gap-2.5 py-[7px] text-[12px] text-cyber-text-secondary">
                <span className="w-[7px] h-[7px] rounded-full flex-shrink-0" style={{ backgroundColor: color }} aria-hidden="true">
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span>{name}</span>
                    <span className="ml-auto flex items-center gap-1.5 text-[11px]">
                      <i className={`w-[7px] h-[7px] rounded-full ${TONE_DOT[accountTone(acc)]}`} />
                      {accountSummaryLabel(acc)}
                    </span>
                  </div>
                  {lastVerified && (
                    <div className="text-[10px] text-cyber-text-muted truncate">
                      {t('header.lastVerified')} {lastVerified}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      <button
        type="button"
        onClick={onGoAccounts}
        className="mt-2.5 w-full flex items-center justify-center gap-1 rounded-[10px] border border-cyber-border-default bg-transparent px-3 py-2 text-[12px] text-cyber-text-primary hover:border-brand hover:text-brand-strong transition-colors"
      >
        {t('header.goAccounts')}
        <ChevronRight className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

export function Header({ viewMode, onNavigate }: HeaderProps) {
  const { t } = useTranslation()
  const { accounts, loading, initialLoaded, error } = useAccounts()
  const [accountOpen, setAccountOpen] = useState(false)
  const accountRef = useRef<HTMLDivElement>(null)

  const badge: LoginBadge = loginBadgeFrom(accounts, { loading, initialLoaded, error })
  const badgeDot =
    badge.kind === 'summary'
      ? badge.tone === 'ok'
        ? 'bg-[#4fa179]'
        : badge.tone === 'warn'
          ? 'bg-[#d69b50]'
          : 'bg-[#98aaba]'
      : BADGE_DOT[badge.kind]
  const badgeText =
    badge.kind === 'checking'
      ? t('header.accountChecking')
      : badge.kind === 'unavailable'
        ? t('header.accountUnavailable')
        : `${badge.verified}/${badge.total}`

  // 提醒登录失效和未登录平台。
  // 去重存储是模块级（lib/accounts）：轮询与 React StrictMode 双挂载都
  // 不会重复提醒。
  const prevAccountsRef = useRef<AccountStatusInfo[] | null>(null)

  useEffect(() => {
    const prev = prevAccountsRef.current
    prevAccountsRef.current = accounts
    if (!accounts) return

    // 1) 由已验证降为 expired/login_required → 每个降级事件只提醒一次
    //    （模块级去重集合：轮询与 StrictMode 双挂载都不会重复）。
    const events = loginExpiryEvents(prev, accounts)
    for (const ev of events) {
      const key = loginExpiryToastKey(ev.platform, ev.lastVerifiedAt)
      if (wasLoginExpiryNotified(key)) continue
      markLoginExpiryNotified(key)
      toast(t('header.loginExpiredToast', { label: ev.label }), {
        action: {
          label: t('header.goAccounts'),
          onClick: () => onNavigate('accounts'),
        },
      })
    }

    // 2) 首次加载完成且存在未登录平台 → 一次性低干扰提醒
    if (initialLoaded && prev === null) {
      const n = unverifiedWarningCount(accounts)
      if (n > 0 && consumeUnverifiedWarning()) {
        toast(t('header.unverifiedWarningToast', { count: n }))
      }
    }
  }, [accounts, initialLoaded, onNavigate, t])

  // 点击外部关闭账号浮层
  useEffect(() => {
    if (!accountOpen) return
    const onClick = (e: MouseEvent) => {
      if (accountRef.current && !accountRef.current.contains(e.target as Node)) {
        setAccountOpen(false)
      }
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [accountOpen])

  const navItems: { key: ViewMode; label: string; section?: SettingsSection }[] = [
    { key: 'search', label: '首页' },
    { key: 'favorites', label: '收藏' },
    { key: 'history', label: '历史' },
    { key: 'accounts', label: '设置', section: 'search' },
  ]

  return (
    <header className="app-header site-header bg-cyber-bg-primary">
      <div className="header-inner">
        {/* 字标：应用 icon + 文字，品牌色只出现在交互状态 */}
        <div className="brand-link" aria-label="四野，聚合搜索">
          <img className="brand-mark" src="/siye-icon.png" alt="" width={30} height={30} />
          <strong className="brand-name">{t('brand.name')}</strong>
          <span className="brand-caption">聚合搜索</span>
        </div>

        {/* 导航 */}
        <nav className="nav" aria-label="主导航">
          {navItems.map(({ key, label, section }) => (
            <button
              key={key}
              type="button"
              onClick={() => onNavigate(key, section)}
              className={`${
                viewMode === key
                  ? 'active'
                  : ''
              }`}
            >
              {label}
            </button>
          ))}
        </nav>

        {/* 右侧：本地服务 / 登录状态 / 主题 / 语言 / 帮助 */}
        <div className="nav header-actions">
          <span className="nav-separator" aria-hidden="true" />
          <div className="relative account-link" ref={accountRef}>
            <button
              type="button"
              onClick={() => setAccountOpen((v) => !v)}
              title={badge.kind === 'summary' && badge.stale ? t('header.staleHint') : undefined}
              className={`account-status-link ${
                accountOpen
                  ? 'text-brand-strong bg-brand-soft'
                  : 'text-cyber-text-muted hover:text-brand-strong'
              } ${badge.kind === 'summary' && badge.stale ? 'opacity-70' : ''}`}
            >
              <i className={`w-2 h-2 rounded-full ${badgeDot}`} />
              <span>{t('header.account')}</span>
              <span className="font-semibold">{badgeText}</span>
            </button>
            {accountOpen && (
              <AccountPopover
                accounts={accounts}
                onGoAccounts={() => { setAccountOpen(false); onNavigate('accounts', 'accounts'); }}
              />
            )}
          </div>

          {viewMode === 'search' && <button
            type="button"
            onClick={() => onNavigate('accounts', 'appearance')}
            className="customize-link"
          >
            自定义
          </button>}

          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
