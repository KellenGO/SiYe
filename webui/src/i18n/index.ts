import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

// 中文翻译
import zhCommon from './locales/zh-CN/common.json'
import zhLicense from './locales/zh-CN/license.json'

// 英文翻译
import enCommon from './locales/en-US/common.json'
import enLicense from './locales/en-US/license.json'

const resources = {
  'zh-CN': {
    common: zhCommon,
    license: zhLicense,
  },
  'en-US': {
    common: enCommon,
    license: enLicense,
  },
}

const ready = i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'zh-CN',
    // 没有 supportedLngs 时，i18next 只认**完全匹配**的资源键：
    // navigator 给 'en' / 'en-GB' 时会整体回落中文，只有精确的 'en-US' 生效。
    // 英文用户里绝大多数浏览器报的都不是 'en-US'。
    supportedLngs: ['zh-CN', 'en-US'],
    // 把 en-GB / en 之类统一归并到 en-US，zh-TW / zh 归并到 zh-CN。
    load: 'currentOnly',
    nonExplicitSupportedLngs: false,
    defaultNS: 'common',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'mediacrawler_language',
    },
  })

/**
 * 语言切换后同步 <html lang> 与标签页标题。
 * index.html 里的 lang="zh-CN" 是写死的：不跟着改，屏幕阅读器会用中文发音规则
 * 念英文界面，浏览器的"是否翻译此页"也可能误判。
 */
function applyDocumentLanguage() {
  if (typeof document === 'undefined') return
  // 必须用 resolvedLanguage：浏览器可能报 en-GB / zh-TW，把这些原样写进
  // `<html lang>` 会让屏幕阅读器按一套界面里并不存在的发音规则朗读。
  document.documentElement.lang = i18n.resolvedLanguage || i18n.language || 'zh-CN'
  document.title = `${i18n.t('brand.name')} · ${i18n.t('brand.subtitle')}`
}

i18n.on('languageChanged', applyDocumentLanguage)
void ready.then(() => applyDocumentLanguage())

export default i18n
