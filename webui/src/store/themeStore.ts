import { create } from 'zustand'

type Theme = 'light' | 'dark' | 'system'

/** 主题色只管色相；深浅（light / dark / system）是独立的开关，两者相乘。 */
/** 色块用的是浅色模式下的品牌色；深色模式的值由 CSS 的 data-accent 块给。 */
export const ACCENTS = [
  { key: 'azure', label: '湛蓝', swatch: '#6573ff' },
  { key: 'graphite', label: '石墨', swatch: '#4b5563' },
  { key: 'pine', label: '松绿', swatch: '#3d8a6a' },
  { key: 'teal', label: '青碧', swatch: '#2a949a' },
  { key: 'violet', label: '紫藤', swatch: '#7c6ac7' },
  { key: 'rose', label: '玫瑰', swatch: '#c75474' },
  { key: 'wine', label: '酒红', swatch: '#9b3c44' },
  { key: 'ochre', label: '赭橙', swatch: '#c17429' },
] as const

export type Accent = (typeof ACCENTS)[number]['key']

interface ThemeState {
  theme: Theme
  resolvedTheme: 'light' | 'dark'
  accent: Accent
  setTheme: (theme: Theme) => void
  setAccent: (accent: Accent) => void
}

const THEME_KEY = 'mediacrawler_theme'
const ACCENT_KEY = 'mediacrawler_accent'

function getSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  const stored = localStorage.getItem(THEME_KEY) as Theme | null
  if (stored && ['light', 'dark', 'system'].includes(stored)) {
    return stored
  }
  return 'light' // Default to light
}

function applyTheme(resolved: 'light' | 'dark') {
  const root = document.documentElement
  if (resolved === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
}

function getStoredAccent(): Accent {
  if (typeof window === 'undefined') return 'azure'
  const stored = localStorage.getItem(ACCENT_KEY)
  const match = ACCENTS.find((item) => item.key === stored)
  return match ? match.key : 'azure'
}

/** 主题色以 data-accent 落在 <html> 上；CSS 侧每个 accent 只覆盖品牌色那一组变量。 */
function applyAccent(accent: Accent) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.accent = accent
}

function resolveTheme(theme: Theme): 'light' | 'dark' {
  return theme === 'system' ? getSystemTheme() : theme
}

// Initialize theme immediately to prevent flash
const initialTheme = getStoredTheme()
const initialResolved = resolveTheme(initialTheme)
const initialAccent = getStoredAccent()
if (typeof window !== 'undefined') {
  applyTheme(initialResolved)
  applyAccent(initialAccent)
}

export const useThemeStore = create<ThemeState>((set) => ({
  theme: initialTheme,
  resolvedTheme: initialResolved,
  accent: initialAccent,

  setTheme: (theme) => {
    const resolved = resolveTheme(theme)
    // 存储被禁用（无痕模式 / 站点数据被禁）时 setItem 会抛。以前整条链路
    // 一起中断：主题没应用、界面毫无反应，用户以为点坏了。
    // 现在先应用主题，写盘失败只影响"下次启动记不记得"。
    applyTheme(resolved)
    set({ theme, resolvedTheme: resolved })
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      /* 只影响持久化，不影响本次切换 */
    }
  },

  setAccent: (accent) => {
    applyAccent(accent)
    set({ accent })
    try {
      localStorage.setItem(ACCENT_KEY, accent)
    } catch {
      /* 同上 */
    }
  },
}))

// Listen for system theme changes
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    const state = useThemeStore.getState()
    if (state.theme === 'system') {
      const resolved = e.matches ? 'dark' : 'light'
      applyTheme(resolved)
      useThemeStore.setState({ resolvedTheme: resolved })
    }
  })
}
