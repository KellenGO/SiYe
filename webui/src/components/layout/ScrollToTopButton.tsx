import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

/** 滚动超过这么多像素才出现：首页、设置这类短页面不该看到它。 */
const REVEAL_AFTER = 400

/**
 * 返回顶部：固定在视口右下角，滚过一屏后淡入。
 *
 * 收藏页和结果页一次只渲染 100 条、点「显示更多」越叠越长，滚到底以后要回到
 * 顶部得一路拉回去，所以给一个常驻在右侧空白处的一键回顶。
 */
export function ScrollToTopButton() {
  const { t } = useTranslation()
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > REVEAL_AFTER)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
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
