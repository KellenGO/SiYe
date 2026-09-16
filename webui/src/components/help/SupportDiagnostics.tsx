import { useState } from "react";
import { Copy, Download, ExternalLink, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { buildSupportReport, collectSupportDiagnostics } from "@/lib/supportDiagnostics";
import { version } from "../../../package.json";

export function SupportDiagnostics() {
  const { t } = useTranslation();
  const [report, setReport] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const capture = async () => {
    setBusy(true);
    setMessage("");
    try {
      const sources = await collectSupportDiagnostics();
      const text = buildSupportReport(sources, version, navigator.userAgent);
      setReport(text);
      try {
        await navigator.clipboard.writeText(text);
        setMessage(t("support.copied"));
      } catch { setMessage(t("support.copyFailed")); }
    } catch { setMessage(t("support.failed")); }
    finally { setBusy(false); }
  };
  const download = () => {
    try {
      const url = URL.createObjectURL(new Blob([report], { type: "application/json;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `SiYe-diagnostics-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setMessage(t("support.downloadFailed")); }
  };
  return <section className="help-section wide" aria-labelledby="support-title">
    <h2 id="support-title">{t("support.title")}</h2>
    <p>{t("support.description")}</p>
    <p>{t("support.privacy")}</p>
    <div className="button-row">
      <button type="button" className="btn primary" disabled={busy} onClick={() => void capture()}>
        {busy ? <Loader2 className="animate-spin" /> : <Copy />}{busy ? t("support.collecting") : t("support.copy")}
      </button>
      {report && <button type="button" className="btn" disabled={busy} onClick={download}><Download />{t("support.download")}</button>}
      <a className="btn" href="https://github.com/KellenGO/SiYe/issues/new?template=bug_report.md" target="_blank" rel="noreferrer">{t("support.feedback")}<ExternalLink /></a>
    </div>
    <p className="support-message" role="status">{message}</p>
    {report && <div className="support-preview">
      <label htmlFor="support-report">{t("support.preview")}</label>
      <textarea id="support-report" readOnly value={report} rows={10} spellCheck={false} onFocus={(event) => event.currentTarget.select()} />
      <p>{t("support.snapshotHint")}</p>
    </div>}
  </section>;
}
