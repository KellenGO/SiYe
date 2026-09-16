import { useState } from "react";
import { ArrowRight, Check, Compass } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GUIDE_ROUTES, type GuideState } from "@/lib/onboarding";

interface GettingStartedProps {
  step: GuideState;
  onGoTo: (step: number) => void;
  onDismiss: (permanent: boolean, completed?: boolean) => void;
}

export function GettingStarted({ step, onGoTo, onDismiss }: GettingStartedProps) {
  const { t } = useTranslation();
  const [neverAgain, setNeverAgain] = useState(false);
  if (step === null) return null;
  const steps = [
    { title: t("onboarding.connectTitle"), description: t("onboarding.connectBody"), action: t("onboarding.connectAction") },
    { title: t("onboarding.searchTitle"), description: t("onboarding.searchBody"), action: t("onboarding.searchAction") },
    { title: t("onboarding.saveTitle"), description: t("onboarding.saveBody"), action: t("onboarding.saveAction") },
    { title: t("onboarding.helpTitle"), description: t("onboarding.helpBody"), action: t("onboarding.helpAction") },
  ];
  const current = step >= 0 ? steps[step] : null;
  return (
    <section className="getting-started" aria-label={t("onboarding.label")}>
      <div className="guide-heading">
        <span className="guide-symbol" aria-hidden="true"><Compass /></span>
        <div>
          <p className="guide-eyebrow">{current ? t("onboarding.progress", { current: step + 1, total: steps.length }) : t("onboarding.welcome")}</p>
          <h2>{current?.title ?? t("onboarding.title")}</h2>
        </div>
      </div>
      <p className="guide-description" aria-live="polite">{current?.description ?? t("onboarding.description")}</p>
      {current && <nav className="guide-steps" aria-label={t("onboarding.steps")}>
        {steps.map((item, index) => <button key={GUIDE_ROUTES[index]} type="button" aria-current={step === index ? "step" : undefined}
          onClick={() => onGoTo(index)}><span>{index + 1}</span>{item.title}</button>)}
      </nav>}
      <div className="guide-footer">
        <div className="button-row">
          {!current ? <button type="button" className="btn primary" onClick={() => onGoTo(0)}>{t("onboarding.start")}<ArrowRight /></button> : <>
            <button type="button" className="btn" onClick={() => onGoTo(step)}>{current.action}</button>
            {step > 0 && <button type="button" className="btn ghost" onClick={() => onGoTo(step - 1)}>{t("onboarding.previous")}</button>}
            <button type="button" className="btn primary" onClick={() => step === steps.length - 1 ? onDismiss(true, true) : onGoTo(step + 1)}>
              {step === steps.length - 1 ? <>{t("onboarding.finish")}<Check /></> : <>{t("onboarding.next")}<ArrowRight /></>}
            </button>
            {step === 2 && <button type="button" className="btn ghost" onClick={() => { window.location.hash = "#/search"; }}>{t("onboarding.backToResults")}</button>}
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
