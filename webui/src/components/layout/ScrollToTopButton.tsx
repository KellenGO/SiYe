import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

/** 收藏等页面保持原有阈值；结果和历史页更早显示入口。 */
const REVEAL_AFTER = 400
const LONG_LIST_REVEAL_AFTER = 160

/**
 * 返回顶部：固定在视口右下角，列表向下滚动后淡入。
 *
 * 收藏页和结果页一次只渲染 100 条、点「显示更多」越叠越长，滚到底以后要回到
 * 顶部得一路拉回去，所以给一个常驻在右侧空白处的一键回顶。
 */
export function ScrollToTopButton() {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    // 阈值只取决于"当前页是不是结果/历史页"，而页面的存在性不会随滚动变化。
    // 以前每个 scroll 事件都 querySelectorAll + 读 offsetHeight —— 那是强制
    // 同步布局，滚一下就要重新排版一次。现在按需重算：路由/滚动容器变化时
    // 才重新判断，滚动事件里只比较一个数字。
    let threshold = REVEAL_AFTER
    let lastThresholdAt = 0
    const resolveThreshold = () => {
      const now = Date.now()
      // 最多每 500ms 复核一次，避免频繁布局读取，同时能跟上页面切换。
      if (now - lastThresholdAt < 500) return threshold
      lastThresholdAt = now
      const inResultsOrHistory = [...document.querySelectorAll<HTMLElement>('.search-shell, .history-page')].some((element) => element.offsetHeight > 0)
      threshold = inResultsOrHistory ? LONG_LIST_REVEAL_AFTER : REVEAL_AFTER
      return threshold
    }
    const onScroll = () => {
      setVisible(window.scrollY > resolveThreshold())
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('hashchange', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('hashchange', onScroll)
    }
  }, [])

  const label = t('action.backToTop')

  return (
    <button
      type="button"
      className={`scroll-top${visible ? ' is-visible' : ''}`}
      aria-label={label}
      title={label}
      aria-hidden={!visible}
      tabIndex={visible ? 0 : -1}
      onClick={() => {
        const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' })
      }}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 19V5" />
        <path d="m5 12 7-7 7 7" />
      </svg>
    </button>
  )
}
