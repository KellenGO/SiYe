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
function applyDocumentLanguage(language: string) {
  if (typeof document === 'undefined') return
  document.documentElement.lang = language
  document.title = `${i18n.t('brand.name')} · ${i18n.t('brand.subtitle')}`
}

i18n.on('languageChanged', applyDocumentLanguage)
void ready.then(() => applyDocumentLanguage(i18n.language))

export default i18n
