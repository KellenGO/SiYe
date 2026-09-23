export const GUIDE_PREFERENCE_KEY = "siye_onboarding_preference_v1";
export const GUIDE_SESSION_KEY = "siye_onboarding_session_v1";

export interface GuideStep {
  key: string;
  route: string;
  /**
   * 可选：这一步要在页面上高亮哪个元素（CSS 选择器）。
   * 带它的步骤会浮出一个跟着滚动走的框选高亮 + 一句提示；不带就只在卡片里讲。
   * 目标元素上要有稳定的 `data-tour` 锚点，别依赖会随样式变动的 class
   * （`tests/test_webui_ui_contract.py` 会核对锚点真的存在）。
   */
  highlight?: string;
}

/**
 * 教程步骤的单一来源：一步一页，`route` 是这一步要落到哪个真实页面。
 * 文案键约定为 `onboarding.<key>Title` / `Body` / `Action`，
 * 带 `highlight` 的步骤还要有 `onboarding.<key>Hint`（给高亮框旁边那句短提示）；
 * 两种语言都要写全（`tests/test_webui_ui_contract.py` 里有守卫回查 locale）。
 */
export const GUIDE_STEPS = [
  { key: "connect", route: "#/settings/accounts" },
  // 平台勾选框的说明在「认识首页」那一步的正文里，所以高亮跟着那一步走。
  { key: "home", route: "#/", highlight: '[data-tour="search-scope"]' },
  { key: "search", route: "#/search" },
  { key: "save", route: "#/favorites/local" },
  { key: "history", route: "#/history" },
  { key: "appearance", route: "#/settings/appearance" },
  { key: "help", route: "#/help" },
] as const satisfies readonly GuideStep[];

export type GuideStepKey = (typeof GUIDE_STEPS)[number]["key"];

/** 取这一步要高亮的元素选择器；不需要高亮的步骤返回 undefined。 */
export function guideHighlight(step: GuideStep): string | undefined {
  return step.highlight;
}

/** -1 是邀请卡；null 是隐藏；0…GUIDE_STEPS.length-1 是跟随的真实页面。 */
export type GuideState = number | null;
export interface GuideStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function read(storage: GuideStorage | null, key: string): string | null {
  try { return storage?.getItem(key) ?? null; } catch { return null; }
}

export function writeGuidePreference(storage: GuideStorage | null, key: string, value: string): boolean {
  try {
    if (!storage) return false;
    storage.setItem(key, value);
    return true;
  } catch { return false; }
}

export function initialGuideState(local: GuideStorage | null, session: GuideStorage | null): GuideState {
  const current = read(session, GUIDE_SESSION_KEY);
  // 步数按 GUIDE_STEPS 长度校验：再加一步时不用回头改这里。
  if (current !== null && /^\d+$/.test(current) && Number(current) < GUIDE_STEPS.length) return Number(current);
  if (current === "hidden") return null;
  // 显式重开的教程可以继续，即使此前已永久关闭。
  const preference = read(local, GUIDE_PREFERENCE_KEY);
  return preference === "dismissed" || preference === "completed" ? null : -1;
}
