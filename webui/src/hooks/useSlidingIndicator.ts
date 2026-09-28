import { useLayoutEffect, useRef, useState } from 'react'

interface IndicatorPosition {
  left: number
  top: number
  width: number
  height: number
}

/** Follow the selected button even when labels or responsive layout change size. */
export function useSlidingIndicator<T extends HTMLElement>(activeKey: string) {
  const containerRef = useRef<T>(null)
  const [position, setPosition] = useState<IndicatorPosition | null>(null)

  useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return

    const update = () => {
      const active = container.querySelector<HTMLElement>('[data-indicator-active="true"]')
      if (!active || !active.offsetWidth) return
      const next = {
        left: active.offsetLeft,
        top: active.offsetTop,
        width: active.offsetWidth,
        height: active.offsetHeight,
      }
      setPosition((current) => current?.left === next.left
        && current.top === next.top
        && current.width === next.width
        && current.height === next.height ? current : next)
    }

    update()
    const observer = new ResizeObserver(update)
    observer.observe(container)
    container.querySelectorAll('button').forEach((button) => observer.observe(button))
    window.addEventListener('resize', update)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [activeKey])

  return { containerRef, position }
}
