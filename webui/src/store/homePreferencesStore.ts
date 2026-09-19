import { create } from 'zustand'

export type HomeMode = 'min' | 'full'

interface HomePreferencesState {
  mode: HomeMode
  history: boolean
  trending: boolean
  setMode: (mode: HomeMode) => void
  setSection: (section: 'history' | 'trending', visible: boolean) => void
}

const STORAGE_KEY = 'siye-home-preferences'

function readPreferences(): Pick<HomePreferencesState, 'mode' | 'history' | 'trending'> {
  if (typeof window === 'undefined') return { mode: 'full', history: true, trending: true }
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as Partial<HomePreferencesState>
    return {
      mode: value.mode === 'min' ? 'min' : 'full',
      history: typeof value.history === 'boolean' ? value.history : true,
      // 旧版本存的是 recent（"最近搜到"板块）；那一块已被热搜卡片取代，
      // 读不到 trending 就用默认值，不把旧键当开关继承。
      trending: typeof value.trending === 'boolean' ? value.trending : true,
    }
  } catch {
    return { mode: 'full', history: true, trending: true }
  }
}

function writePreferences(state: Pick<HomePreferencesState, 'mode' | 'history' | 'trending'>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Storage can be unavailable in hardened browsers; the in-memory preference still works.
  }
}

const initial = readPreferences()

export const useHomePreferencesStore = create<HomePreferencesState>((set) => ({
  ...initial,
  setMode: (mode) => set((state) => {
    const next = { ...state, mode }
    writePreferences(next)
    return { mode }
  }),
  setSection: (section, visible) => set((state) => {
    const next = { ...state, [section]: visible, ...(visible ? { mode: 'full' as const } : {}) }
    writePreferences(next)
    return { [section]: visible, ...(visible ? { mode: 'full' as const } : {}) }
  }),
}))
