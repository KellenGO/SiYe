import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import * as api from "@/lib/researchApi";
import { spaceError } from "@/lib/spaceNotes";

const PROVIDERS = [
  { id: "deepseek", name: "DeepSeek", url: "https://api.deepseek.com", model: "deepseek-flash" },
  { id: "gemini", name: "Gemini", url: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-3.8-flash" },
  { id: "openai", name: "OpenAI", url: "https://api.openai.com/v1", model: "" },
];

export function ResearchSettings() {
  const { t } = useTranslation();
  const config = useQuery({ queryKey: ["research-config"], queryFn: api.getResearchConfig, retry: false, refetchOnWindowFocus: false });
  const [initialized, setInitialized] = useState(false);
  const [provider, setProvider] = useState("deepseek");
  const [protocol, setProtocol] = useState<api.ResearchConfig["protocol"]>("openai");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [web, setWeb] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pending = useRef(false);
  useEffect(() => { if (config.data) { setProtocol(config.data.protocol); setProvider(config.data.protocol === "anthropic" ? "anthropic" : PROVIDERS.find((row) => row.url === config.data!.base_url.replace(/\/$/, ""))?.id ?? "custom"); setBaseUrl(config.data.base_url); setModel(config.data.model); setInitialized(true); } }, [config.data]);
  const run = async (action: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (cause) { setError(spaceError(cause)); }
    finally { pending.current = false; setBusy(false); }
  };
  const save = async () => { await api.saveResearchConfig(baseUrl, model, key || undefined, protocol); setKey(""); await config.refetch(); };
  let sameService = false;
  try { sameService = new URL(baseUrl).origin === new URL(config.data?.base_url ?? "").origin; } catch { /* Invalid addresses are rejected when saved. */ }
  const valid = initialized && !!model.trim() && (!!key.trim() || (sameService && !!config.data?.has_key));
  return <section className="settings-section-enter research-settings">
    <div className="settings-title"><h2>{t("research.configure")}</h2><p>{t("research.sharedConfig")}</p></div>
    {config.data && <p className="space-research-hint">{protocol === "openai" ? t("research.openaiReady") : t(config.data.runtime.sdk_available && config.data.runtime.cli_available ? "research.runtimeReady" : "research.runtimeMissing")}</p>}
    <div className="research-settings-fields">
      <label>{t("research.provider")}<select className="field" aria-label={t("research.provider")} value={provider} disabled={busy || !initialized} onChange={(event) => {
        const next = PROVIDERS.find((row) => row.id === event.target.value);
        setProvider(event.target.value); setKey("");
        setProtocol(event.target.value === "anthropic" ? "anthropic" : "openai");
        if (next) { setBaseUrl(next.url); setModel(next.model); } else if (event.target.value === "anthropic") { setBaseUrl("https://api.anthropic.com"); setModel(""); }
      }}>
        {PROVIDERS.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
        <option value="custom">{t("research.customProvider")}</option><option value="anthropic">{t("research.legacyProvider")}</option>
      </select></label>
      <p className="space-research-hint">{t("research.protocolHint")}</p>
      <label>{t("research.address")}<input className="field" type="url" value={baseUrl} disabled={busy || !initialized} onChange={(event) => { setBaseUrl(event.target.value); if (protocol === "openai") setProvider("custom"); }} /></label>
      <label>{t("research.model")}<input className="field" value={model} disabled={busy || !initialized} onChange={(event) => setModel(event.target.value)} /></label>
      <label>{t("research.key")}<input className="field" type="password" autoComplete="off" value={key} placeholder={sameService ? config.data?.key_mask ?? "" : t("research.providerKey")} disabled={busy || !initialized} onChange={(event) => setKey(event.target.value)} /></label>
      <p className="space-research-hint">{t("research.keyHint")}</p>
      <label className="space-research-web"><input type="checkbox" checked={web} disabled={busy || !initialized} onChange={(event) => setWeb(event.target.checked)} />{t("research.testWeb")}</label>
      <p className="space-research-hint">{t("research.testWebHint")}</p>
    </div>
    {(error || config.error) && <p role="alert">{error || spaceError(config.error)}</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="space-research-actions">
      <button type="button" className="btn" disabled={busy || !config.data?.has_key} onClick={() => void run(async () => { await api.deleteResearchConfig(); setKey(""); await config.refetch(); })}>{t("research.clearConfig")}</button>
      <button type="button" className="btn" disabled={busy || !valid} onClick={() => void run(async () => { await save(); setNotice(t("research.configSaved")); })}>{t("research.saveConfig")}</button>
      <button type="button" className="btn primary" disabled={busy || !valid} onClick={() => void run(async () => { await save(); await api.testResearchConnection(web); setNotice(t(web ? "research.webTestPassed" : "research.testPassed")); })}>{t("research.test")}</button>
    </div>
  </section>;
}
