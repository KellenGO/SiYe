export const GUIDE_PREFERENCE_KEY = "siye_onboarding_preference_v1";
export const GUIDE_SESSION_KEY = "siye_onboarding_session_v1";
export const GUIDE_ROUTES = ["#/settings/accounts", "#/search", "#/favorites/local", "#/help"] as const;

/** -1 is the invitation; null is hidden; 0–3 are the guided pages. */
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
  // An explicitly restarted tutorial can continue even after permanent dismissal.
  if (current !== null && /^[0-3]$/.test(current)) return Number(current);
  if (current === "hidden") return null;
  const preference = read(local, GUIDE_PREFERENCE_KEY);
  return preference === "dismissed" || preference === "completed" ? null : -1;
}
