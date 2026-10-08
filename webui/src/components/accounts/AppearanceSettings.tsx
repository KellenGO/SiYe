import { Check } from "lucide-react";
import { ACCENTS, useThemeStore } from "@/store/themeStore";
import { useHomePreferencesStore } from "@/store/homePreferencesStore";

export function AppearanceSettings() {
  const { theme, setTheme, accent, setAccent } = useThemeStore();
  const homePreferences = useHomePreferencesStore();
  return (
<section className="settings-section-enter">
        <div className="settings-title"><h2>外观与首页</h2><p>安静一点，或多一些内容。按你的习惯来。</p></div>
        <div className="setting-label">主题色</div>
        <div className="accent-grid" role="group" aria-label="主题色">
          {ACCENTS.map((item) => <button key={item.key} type="button" className="accent-swatch"
            title={item.label} aria-label={`主题色 ${item.label}`} aria-pressed={accent === item.key}
            style={{ ["--swatch" as string]: item.swatch }}
            onClick={() => setAccent(item.key)} />)}
        </div>
        <p className="accent-name">{ACCENTS.find((item) => item.key === accent)?.label}　·　深浅在下面单独选</p>
        <div className="theme-choices">
          {(["light", "dark"] as const).map((value) => <button key={value} type="button" className="theme-card" aria-pressed={theme === value} onClick={() => setTheme(value)}>
            <div className={`theme-preview ${value === "dark" ? "night" : ""}`} aria-hidden="true" />
            <span>{value === "dark" ? "深色" : "浅色"}{theme === value && <Check />}</span>
          </button>)}
        </div>
        <div className="setting-row"><div><div className="setting-label">首页模式</div><p className="setting-desc">极简留白，实用展示最近的探索</p></div><div className="segmented">
          <button type="button" className="segment" aria-pressed={homePreferences.mode === "min"} onClick={() => homePreferences.setMode("min")}>极简</button>
          <button type="button" className="segment" aria-pressed={homePreferences.mode === "full"} onClick={() => homePreferences.setMode("full")}>实用</button>
        </div></div>
        <div className="setting-row"><div><div className="setting-label">最近搜索</div><p className="setting-desc">回到上一次搜索过的关键词</p></div><button type="button" className="switch" role="switch" aria-checked={homePreferences.history} aria-label="首页显示最近搜索" onClick={() => homePreferences.setSection("history", !homePreferences.history)} /></div>
        <div className="setting-row"><div><div className="setting-label">热搜</div><p className="setting-desc">看看各平台在热什么，点一条直接搜</p></div><button type="button" className="switch" role="switch" aria-checked={homePreferences.trending} aria-label="首页显示热搜" onClick={() => homePreferences.setSection("trending", !homePreferences.trending)} /></div>
        {/* 回首页的出口移到顶栏「自定义」原来的位置，这里只留说明文字 */}
        <div className="setting-footer"><span>主题与首页偏好会保存在当前浏览器</span></div>
        <div className="info-box"><p>极简模式会暂时隐藏首页板块，不清除板块选择。开启任一板块会自动切回实用模式。</p></div>
      </section>
  );
}
