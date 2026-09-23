import { useState } from "react";
import { ArrowRight, Check, Compass } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GUIDE_STEPS, type GuideState } from "@/lib/onboarding";

interface GettingStartedProps {
  step: GuideState;
  onGoTo: (step: number) => void;
  onDismiss: (permanent: boolean, completed?: boolean) => void;
}

export function GettingStarted({ step, onGoTo, onDismiss }: GettingStartedProps) {
  const { t } = useTranslation();
  const [neverAgain, setNeverAgain] = useState(false);
  if (step === null) return null;
  const total = GUIDE_STEPS.length;
  const current = step >= 0 ? GUIDE_STEPS[step] : null;
  /** 收藏那一步的「回搜索结果」要跳回搜索步骤本身（而不是直接改地址）。 */
  const searchStep = GUIDE_STEPS.findIndex((item) => item.key === "search");
  return (
    <section className="getting-started" aria-label={t("onboarding.label")}>
      <div className="guide-heading">
        <span className="guide-symbol" aria-hidden="true"><Compass /></span>
        <div>
          <p className="guide-eyebrow">{current ? t("onboarding.progress", { current: step + 1, total }) : t("onboarding.welcome")}</p>
          <h2>{current ? t(`onboarding.${current.key}Title`) : t("onboarding.title")}</h2>
        </div>
      </div>
      <p className="guide-description" aria-live="polite">{current ? t(`onboarding.${current.key}Body`) : t("onboarding.description")}</p>
      {current && <nav className="guide-steps" aria-label={t("onboarding.steps")}>
        {GUIDE_STEPS.map((item, index) => <button key={item.key} type="button" aria-current={step === index ? "step" : undefined}
          onClick={() => onGoTo(index)}><span>{index + 1}</span>{t(`onboarding.${item.key}Title`)}</button>)}
      </nav>}
      <div className="guide-footer">
        <div className="button-row">
          {!current ? <button type="button" className="btn primary" onClick={() => onGoTo(0)}>{t("onboarding.start")}<ArrowRight /></button> : <>
            <button type="button" className="btn" onClick={() => onGoTo(step)}>{t(`onboarding.${current.key}Action`)}</button>
            {step > 0 && <button type="button" className="btn ghost" onClick={() => onGoTo(step - 1)}>{t("onboarding.previous")}</button>}
            <button type="button" className="btn primary" onClick={() => step === total - 1 ? onDismiss(true, true) : onGoTo(step + 1)}>
              {step === total - 1 ? <>{t("onboarding.finish")}<Check /></> : <>{t("onboarding.next")}<ArrowRight /></>}
            </button>
            {current.key === "save" && searchStep >= 0 && <button type="button" className="btn ghost" onClick={() => onGoTo(searchStep)}>{t("onboarding.backToResults")}</button>}
          </>}
        </div>
        <div className="guide-dismiss">
          <label><input type="checkbox" checked={neverAgain} onChange={(event) => setNeverAgain(event.target.checked)} />{t("onboarding.neverAgain")}</label>
          <button type="button" className="text-link" onClick={() => onDismiss(neverAgain)}>{neverAgain ? t("onboarding.close") : t("onboarding.skip")}</button>
        </div>
      </div>
      <p className="guide-footnote">{t("onboarding.replayHint")}</p>
    </section>
  );
}
