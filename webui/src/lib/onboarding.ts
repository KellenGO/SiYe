export const GUIDE_PREFERENCE_KEY = "siye_onboarding_preference_v1";
export const GUIDE_SESSION_KEY = "siye_onboarding_session_v1";

/**
 * 教程步骤的单一来源：一步一页，`route` 是这一步要落到哪个真实页面。
 * 文案键约定为 `onboarding.<key>Title` / `onboarding.<key>Body` / `onboarding.<key>Action`，
 * 两种语言都要写全（`tests/test_webui_ui_contract.py` 里有守卫回查 locale）。
 */
export const GUIDE_STEPS = [
  { key: "connect", route: "#/settings/accounts" },
  { key: "search", route: "#/search" },
  { key: "save", route: "#/favorites/local" },
  { key: "help", route: "#/help" },
] as const;

export type GuideStepKey = (typeof GUIDE_STEPS)[number]["key"];

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
