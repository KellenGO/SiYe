import { Suspense, lazy, useEffect, useState } from 'react'
import { Toaster } from 'sonner'
import { Header, type SettingsSection, type ViewMode } from '@/components/layout/Header'
import { AuthorFooter } from '@/components/layout/AuthorFooter'
import { ScrollToTopButton } from '@/components/layout/ScrollToTopButton'
import { LicenseDisclaimer, isLicenseAccepted } from '@/components/license/LicenseDisclaimer'
import { SearchPage } from '@/components/search/SearchPage'
import { AccountAutoSync } from '@/components/accounts/AccountAutoSync'
import { GettingStarted } from '@/components/help/GettingStarted'
import { useOnboarding } from '@/hooks/useOnboarding'
import { useTranslation } from 'react-i18next'

// 非搜索页按需加载，搜索主页面保持同步加载。
const AccountsPage = lazy(() =>
  import('@/components/accounts/AccountsPage').then((m) => ({ default: m.AccountsPage }))
)
const FavoritesPage = lazy(() =>
  import('@/components/favorites/FavoritesPage').then((m) => ({ default: m.FavoritesPage }))
)
const HistoryPage = lazy(() =>
  import('@/components/history/HistoryPage').then((m) => ({ default: m.HistoryPage }))
)
const HelpPage = lazy(() =>
  import('@/components/help/HelpPage').then((m) => ({ default: m.HelpPage }))
)

/** 浅蓝色 Suspense 占位（不闪屏）。 */
function PageLoading() {
  return (
    <div className="pt-16 flex justify-center">
      <div className="inline-block animate-dsh-spin rounded-full h-8 w-8 border-2 border-sky-300 border-t-transparent" />
    </div>
  )
}

type FavoritesSection = 'local' | 'remote'

function routeFromHash(): { view: ViewMode; settings: SettingsSection; favorites: FavoritesSection; home: boolean } {
  const path = window.location.hash.replace(/^#/, '') || '/'
  if (path.startsWith('/favorites/remote')) return { view: 'favorites', settings: 'search', favorites: 'remote', home: false }
  if (path.startsWith('/favorites')) return { view: 'favorites', settings: 'search', favorites: 'local', home: false }
  if (path.startsWith('/settings/accounts')) return { view: 'accounts', settings: 'accounts', favorites: 'local', home: false }
  if (path.startsWith('/settings/appearance')) return { view: 'accounts', settings: 'appearance', favorites: 'local', home: false }
  if (path.startsWith('/settings')) return { view: 'accounts', settings: 'search', favorites: 'local', home: false }
  if (path.startsWith('/history')) return { view: 'history', settings: 'search', favorites: 'local', home: false }
  if (path.startsWith('/help')) return { view: 'help', settings: 'search', favorites: 'local', home: false }
  if (path.startsWith('/search')) return { view: 'search', settings: 'search', favorites: 'local', home: false }
  return { view: 'search', settings: 'search', favorites: 'local', home: true }
}

function App() {
  const { t } = useTranslation()
  const onboarding = useOnboarding()
  const initialRoute = routeFromHash()
  // Initialize by checking localStorage if license has been accepted
  const [licenseAccepted, setLicenseAccepted] = useState(() => isLicenseAccepted())
  // State for showing disclaimer manually
  const [showDisclaimer, setShowDisclaimer] = useState(false)
  // View mode toggle
  const [viewMode, setViewMode] = useState<ViewMode>(initialRoute.view)
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(initialRoute.settings)
  const [accountsVisited, setAccountsVisited] = useState(initialRoute.view === 'accounts')
  const [compactNotifications, setCompactNotifications] = useState(
    () => window.matchMedia('(max-width: 700px)').matches,
  )
  useEffect(() => {
    const media = window.matchMedia('(max-width: 700px)')
    const sync = () => setCompactNotifications(media.matches)
    media.addEventListener('change', sync)
    return () => media.removeEventListener('change', sync)
  }, [])
  useEffect(() => {
    if (viewMode === 'accounts') setAccountsVisited(true)
  }, [viewMode])
  const [favoritesSection, setFavoritesSection] = useState<FavoritesSection>(initialRoute.favorites)
  const [homeRoute, setHomeRoute] = useState(initialRoute.home)

  useEffect(() => {
    const syncRoute = () => {
      const route = routeFromHash()
      setViewMode(route.view)
      setSettingsSection(route.settings)
      setFavoritesSection(route.favorites)
      setHomeRoute(route.home)
    }
    window.addEventListener('hashchange', syncRoute)
    return () => window.removeEventListener('hashchange', syncRoute)
  }, [])

  const navigate = (mode: ViewMode, section?: SettingsSection) => {
    const nextHash = mode === 'accounts'
      ? `/settings/${section || settingsSection}`
      : mode === 'favorites'
        ? '/favorites/local'
        : mode === 'history'
          ? '/history'
          : mode === 'help'
            ? '/help'
            : '/'
    if (window.location.hash === `#${nextHash}`) {
      const route = routeFromHash()
      setViewMode(route.view)
      setSettingsSection(route.settings)
      setFavoritesSection(route.favorites)
      setHomeRoute(route.home)
    } else {
      window.location.hash = nextHash
    }
  }

  const showSearchResultsRoute = () => {
    setHomeRoute(false)
    if (window.location.hash !== '#/search') window.location.hash = '/search'
  }

  const changeFavoritesSection = (section: FavoritesSection) => {
    setFavoritesSection(section)
    if (window.location.hash !== `#/favorites/${section}`) window.location.hash = `/favorites/${section}`
  }

  const handleLicenseAccept = () => {
    setLicenseAccepted(true)
    setShowDisclaimer(false)
  }

  const handleShowDisclaimer = () => {
    setShowDisclaimer(true)
  }

  return (
    <div className={`min-h-screen flex flex-col relative ${viewMode === 'search' && homeRoute ? 'home-route' : ''} ${onboarding.step !== null ? 'has-onboarding' : ''}`}>
      {/* License Disclaimer Modal - Shows first or when triggered */}
      {(!licenseAccepted || showDisclaimer) && (
        <LicenseDisclaimer onAccept={handleLicenseAccept} />
      )}

      {/* 顶部栏：品牌 / 导航 / 本地服务 / 账号状态 / 主题 / 语言 / 帮助 */}
      {licenseAccepted && !showDisclaimer && (
        <Header viewMode={viewMode} settingsSection={settingsSection} onNavigate={navigate} />
      )}

      {/* 打开程序即自动检测并同步登录状态（结果走 toast，进行中给出细提示） */}
      {licenseAccepted && !showDisclaimer && <AccountAutoSync />}

      {/* Main Area */}
      <main className="flex-1 w-full">
        {licenseAccepted && !showDisclaimer && (
          <div className="app-main-inner">
            <GettingStarted step={onboarding.step} onGoTo={onboarding.goTo} onDismiss={onboarding.dismiss} />
            {!onboarding.preferenceSaved && <p role="status" className="guide-storage-warning">{t("onboarding.storageUnavailable")}</p>}
            <Suspense fallback={<PageLoading />}>
              {viewMode === 'search' ? (
                <SearchPage homeRequested={homeRoute} onSearchStarted={showSearchResultsRoute} onNavigateAccounts={() => navigate('accounts', 'accounts')} />
              ) : viewMode === 'favorites' ? (
                <FavoritesPage activeTab={favoritesSection} onTabChange={changeFavoritesSection} onNavigateAccounts={() => navigate('accounts', 'accounts')} />
              ) : viewMode === 'help' ? (
                <HelpPage onShowDisclaimer={handleShowDisclaimer} onStartGuide={() => onboarding.goTo(0)} />
              ) : viewMode === 'history' ? (
                <HistoryPage />
              ) : null}
            </Suspense>
            {/* Keep active login polling alive when navigating back to search. */}
            {(accountsVisited || viewMode === 'accounts') && <div hidden={viewMode !== 'accounts'}>
              <Suspense fallback={<PageLoading />}>
                <AccountsPage
                  activeSection={settingsSection}
                  onSectionChange={(section) => navigate('accounts', section)}
                  onNavigateHelp={() => navigate('help')}
                />
              </Suspense>
            </div>}
          </div>
        )}
      </main>

      {/* 低调页脚：随页面内容滚动，不遮挡结果 */}
      {licenseAccepted && !showDisclaimer && (
        <AuthorFooter onShowDisclaimer={handleShowDisclaimer} onNavigateHelp={() => navigate('help')} />
      )}

      {/* 列表长了要一键回顶：固定在右侧空白处，滚过一屏才出现 */}
      {licenseAccepted && !showDisclaimer && <ScrollToTopButton />}

      {/* 窄屏的顶部空间留给导航和页面操作；普通通知移到底部，避免挡住按钮。 */}
      <Toaster
        position={compactNotifications ? "bottom-center" : "top-right"}
        toastOptions={{
          className: 'glass-panel text-cyber-text-primary',
          style: {
            borderRadius: '12px',
          },
        }}
      />
    </div>
  )
}

export default App
