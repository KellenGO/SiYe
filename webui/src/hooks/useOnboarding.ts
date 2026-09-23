import { useState } from "react";
import {
  GUIDE_PREFERENCE_KEY, GUIDE_SESSION_KEY, GUIDE_STEPS,
  initialGuideState, writeGuidePreference, type GuideState,
} from "@/lib/onboarding";

function browserStorage(kind: "localStorage" | "sessionStorage"): Storage | null {
  try { return window[kind]; } catch { return null; }
}

export function useOnboarding() {
  const [step, setStep] = useState(() => initialGuideState(browserStorage("localStorage"), browserStorage("sessionStorage")));
  const [preferenceSaved, setPreferenceSaved] = useState(true);

  const change = (next: GuideState) => {
    writeGuidePreference(browserStorage("sessionStorage"), GUIDE_SESSION_KEY, next === null ? "hidden" : String(next));
    setStep(next);
  };
  const goTo = (next: number) => {
    if (!Number.isInteger(next) || next < 0 || next >= GUIDE_STEPS.length) return;
    change(next);
    window.location.hash = GUIDE_STEPS[next].route;
    window.scrollTo({ top: 0 });
  };
  const dismiss = (permanent: boolean, completed = false) => {
    if (permanent || completed) {
      const saved = writeGuidePreference(browserStorage("localStorage"), GUIDE_PREFERENCE_KEY, completed ? "completed" : "dismissed");
      setPreferenceSaved(saved);
    }
    change(null);
  };

  return { step, goTo, dismiss, preferenceSaved };
}
