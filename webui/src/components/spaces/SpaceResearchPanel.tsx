import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Sparkles, Settings2, X } from "lucide-react";
import type { Editor } from "@tiptap/react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/researchApi";
import { fetchSpace } from "@/lib/spacesApi";
import { spaceError, type NoteDocument, type NoteSession } from "@/lib/spaceNotes";

function previewNode(node: NoteDocument, index: number): ReactNode {
  const children = node.content?.map(previewNode);
  if (node.type === "text") {
    const link = node.marks?.find((mark) => mark.type === "link");
    return link ? <a key={index} href={String(link.attrs?.href)} target="_blank" rel="noopener noreferrer">{node.text}</a> : node.text;
  }
  if (node.type === "heading") return node.attrs?.level === 2 ? <h2 key={index}>{children}</h2> : <h3 key={index}>{children}</h3>;
  if (node.type === "paragraph") return <p key={index}>{children}</p>;
  return <div key={index}>{children}</div>;
}

export default function SpaceResearchPanel({ spaceId, editor, session, archived }: {
  spaceId: number; editor: Editor; session: NoteSession; archived: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [baseUrl, setBaseUrl] = useState("https://api.anthropic.com");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [question, setQuestion] = useState("");
  const [web, setWeb] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [job, setJob] = useState<api.ResearchJob | null>(null);
  const jobVersion = useRef(0);
  const inserted = job && session.hasResearch(job.job_id);
  const applied = job && (session.hasSavedResearch(job.job_id) || wasApplied(job.job_id));
  const config = useQuery({ queryKey: ["research-config"], queryFn: api.getResearchConfig, enabled: open, retry: false });
  const latest = useQuery({ queryKey: ["research-latest", spaceId], queryFn: async () => {
    const version = jobVersion.current;
    const next = await api.latestResearch(spaceId);
    if (version === jobVersion.current && next) { setJob(next); setQuestion(next.question); }
    return next;
  }, enabled: open && !job, retry: false, refetchOnWindowFocus: false });
  const preference = useQuery({ queryKey: ["research-web", spaceId], queryFn: () => api.getWebPreference(spaceId), enabled: open, retry: false });
  useEffect(() => { if (config.data) { setBaseUrl(config.data.base_url); setModel(config.data.model); } }, [config.data]);
  useEffect(() => { if (preference.data) setWeb(preference.data.web_enabled); }, [preference.data]);
  useEffect(() => {
    const element = dialog.current;
    if (!element || !open) return;
    element.showModal();
    return () => { element.close(); };
  }, [open]);
  const running = job?.status === "collecting" || job?.status === "analyzing";
  useEffect(() => {
    if (!open || !running || !job) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const version = jobVersion.current;
      try {
        const next = await api.getResearch(job.job_id);
        if (!disposed && version === jobVersion.current) { setJob(next); setError(""); }
      } catch (cause) { if (!disposed) setError(spaceError(cause)); }
      if (!disposed) timer = setTimeout(() => { void poll(); }, 1000);
    };
    timer = setTimeout(() => { void poll(); }, 600);
    return () => { disposed = true; clearTimeout(timer); };
  }, [open, running, job?.job_id]);

  const run = async (operation: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (cause) { setError(spaceError(cause)); }
    finally { pending.current = false; setBusy(false); }
  };
  const start = () => run(async () => {
    if (job && !["ready", "failed", "cancelled"].includes(job.status)) await updateJob(() => api.researchAction(job.job_id, "cancel"));
    await updateJob(() => api.createResearch(spaceId, question, web));
  });
  const updateJob = async (request: () => Promise<api.ResearchJob>) => {
    jobVersion.current += 1;
    setJob(await request());
  };
  const append = () => run(async () => {
    if (!job?.document || applied) return;
    const current = await fetchSpace(spaceId);
    if (current.archived) throw new Error(t("research.archived"));
    if (current.note_revision !== session.revision) {
      if (!session.sync(current.note_document, current.note_revision)) throw new Error(t("research.noteConflict"));
      editor.commands.setContent(session.document, { emitUpdate: false });
    }
    if (editor.view.composing) throw new Error(t("research.finishTyping"));
    if (!inserted && !session.appendResearch(job.job_id, job.document, (content) => editor.chain().insertContentAt(editor.state.doc.content.size, content).run())) throw new Error(t("research.noteConflict"));
    if (!await session.flush()) throw new Error(t("research.appendSaveFailed"));
    setNotice(t("research.appended"));
  });

  const saveConfig = async () => {
    await api.saveResearchConfig(baseUrl, model, key || undefined);
    setKey(""); await config.refetch();
  };

  const progress = !job || job.status === "collecting" ? 0 : job.status === "awaiting_sources" ? 1 : job.status === "analyzing" ? 2 : job.status === "ready" ? 3 : -1;
  const locked = busy || running || job?.status === "awaiting_sources";
  return <div className="space-research-entry">
    <button type="button" className="btn small" onClick={() => { setSettings(false); setOpen(true); }}><Sparkles aria-hidden="true" />{t("research.open")}</button>
    {job && <span>{t(`research.status.${job.status}`)}</span>}
    <dialog ref={dialog} className="space-research-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); setOpen(false); }} onClick={(event) => { if (event.target === event.currentTarget) setOpen(false); }}>
      <section className="space-research" onClick={(event) => event.stopPropagation()}>
        <header className="space-research-head">
          <div><span className="eyebrow">{t("spaces.note")}</span><h2 id={titleId}>{t(settings ? "research.configure" : "research.title")}</h2></div>
          <div className="space-research-actions">
            {!settings && <button type="button" className="btn small" onClick={() => { setSettings(true); setError(""); setNotice(""); }}><Settings2 aria-hidden="true" />{t("research.configure")}</button>}
            <button type="button" className="btn small" aria-label={t("research.close")} onClick={() => setOpen(false)}><X aria-hidden="true" /></button>
          </div>
        </header>
        {!settings && <ol className="space-research-steps" aria-label={t("research.progress")}>
          {["collect", "review", "analyze", "result"].map((step, index) => <li key={step} className={index === progress ? "is-current" : index < progress ? "is-done" : ""} aria-current={index === progress ? "step" : undefined}><span>{index + 1}</span>{t(`research.step.${step}`)}</li>)}
        </ol>}
        <div className="space-research-body">
          {settings ? <div className="space-research-config">
            <p className="space-research-hint">{t("research.sharedConfig")}</p>
            {config.data && <p>{t(config.data.runtime.sdk_available && config.data.runtime.cli_available ? "research.runtimeReady" : "research.runtimeMissing")}</p>}
            <label>{t("research.address")}<input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={locked} /></label>
            <label>{t("research.model")}<input value={model} onChange={(event) => setModel(event.target.value)} disabled={locked} /></label>
            <label>{t("research.key")}<input type="password" autoComplete="off" value={key} placeholder={config.data?.key_mask ?? ""} onChange={(event) => setKey(event.target.value)} disabled={locked} /></label>
            <p className="space-research-hint">{t("research.keyHint")}</p>
            <p className="space-research-hint">{t(web ? "research.taskWebOn" : "research.taskWebOff")}</p>
          </div> : <>
            <p className="space-research-hint">{t("research.intro")}</p>
            {!config.data?.has_key && <p className="space-research-warning">{t("research.setupRequired")}</p>}
            <details className="space-research-options" open={!job || job.status === "failed" || job.status === "cancelled"}>
            <summary hidden={!job}>{t("research.nextSettings")}</summary>
            <label>{t("research.question")}<textarea rows={3} maxLength={2000} value={question} placeholder={t("research.questionHint")} disabled={locked} onChange={(event) => setQuestion(event.target.value)} /></label>
            <label className="space-research-web"><input type="checkbox" checked={web} disabled={busy || !preference.data} onChange={(event) => {
              const enabled = event.target.checked;
              setWeb(enabled);
              void run(async () => {
                try { await api.saveWebPreference(spaceId, enabled); }
                catch (cause) { setWeb(!enabled); throw cause; }
              });
            }} />{t("research.allowWeb")}</label>
            <p className="space-research-hint">{t(web ? "research.webHint" : "research.localHint")}</p>
            </details>
            {job && <div className="space-research-job">
              <div className="space-research-task"><strong role="status">{t(`research.status.${job.status}`)}</strong><span>{t(job.web_enabled ? "research.taskWebOn" : "research.taskWebOff")}</span></div>
              {job.question && <p>{job.question}</p>}
              {running && <p role="status">{job.message}{job.status === "collecting" && ` · ${job.materials.length} / ${job.total_materials}`}</p>}
              {job.error && <p role="alert">{job.error}</p>}
              {job.stale_snapshot && <p className="space-research-warning">{t("research.stale")}</p>}
              {job.materials.length > 0 && <details open={job.status === "awaiting_sources"}>
                <summary>{t("research.coverage")} · {job.materials.length}</summary>
                <div className="space-research-materials">{job.materials.map((item) => <article key={item.key}>
                  <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
                  <div className="space-research-tags">{(["body", "comments", "subtitles"] as const).map((name) => <span key={name} className={`space-research-tag is-${item[name].state}`}>
                    {t(`research.component.${name}`)} · {t(`research.state.${item[name].state}`)}{name !== "body" && item[name].count > 0 && ` ${item[name].count}`}{item[name].truncated && ` · ${t("research.partial")}`}
                  </span>)}</div>
                  {(["body", "comments", "subtitles"] as const).map((name) => item[name].reason && <p key={name} className="space-research-hint">{t(`research.component.${name}`)}：{item[name].reason}</p>)}
                  {item.comments.sort && <p className="space-research-hint">{item.comments.sort}</p>}
                </article>)}</div>
              </details>}
              {job.status === "awaiting_sources" && <p className="space-research-hint">{t("research.reviewHint")}</p>}
              {job.document && <>
                <div className="space-research-preview" aria-label={t("research.preview")}>{job.document.content?.map(previewNode)}</div>
                {job.coverage.some((row) => !row.complete) && <p className="space-research-warning">{t("research.unread")}</p>}
                {job.web_errors.length > 0 && <p>{t("research.webFailed")}</p>}
                {job.cost_usd !== null && <p className="space-research-hint">{t("research.cost", { cost: job.cost_usd.toFixed(4) })}</p>}
                {inserted && !applied && <p className="space-research-warning">{t("research.draftInserted")}</p>}
              </>}
            </div>}
          </>}
        </div>
        <footer className="space-research-footer">
          {(error || config.error || latest.error || preference.error) && <p role="alert">{error || spaceError(config.error || latest.error || preference.error)}</p>}
          {notice && <p role="status">{notice}</p>}
          <div className="space-research-actions">
            {settings ? <>
              <button type="button" className="btn small" disabled={busy} onClick={() => { setSettings(false); setError(""); setNotice(""); }}>{t("research.back")}</button>
              <button type="button" className="btn small" disabled={locked || !config.data?.has_key} onClick={() => void run(async () => { await api.deleteResearchConfig(); setKey(""); await config.refetch(); })}>{t("research.clearConfig")}</button>
              <button type="button" className="btn small" disabled={locked || !model.trim() || (!key.trim() && !config.data?.has_key)} onClick={() => void run(async () => { await saveConfig(); setNotice(t("research.configSaved")); })}>{t("research.saveConfig")}</button>
              <button type="button" className="btn small primary" disabled={locked || !model.trim() || (!key.trim() && !config.data?.has_key)} onClick={() => void run(async () => { await saveConfig(); await api.testResearchConnection(web); setNotice(t(web ? "research.webTestPassed" : "research.testPassed")); })}>{t("research.test")}</button>
            </> : <>
              {(running || job?.status === "awaiting_sources") && <button type="button" className="btn small" disabled={busy} onClick={() => void run(async () => { await updateJob(() => api.researchAction(job!.job_id, "cancel")); })}>{t("research.cancel")}</button>}
              {job && ["awaiting_sources", "failed"].includes(job.status) && <button type="button" className="btn small" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(job.job_id, "retry")); })}>{t("research.retrySources")}</button>}
              {job?.status === "awaiting_sources" ? <button type="button" className="btn small primary" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(job.job_id, "generate")); })}>{t("research.continue")}</button>
              : job?.document ? <>
                <button type="button" className="btn small" disabled={busy || archived || !config.data?.has_key || !preference.data} onClick={() => void start()}>{t("research.regenerate")}</button>
                <button type="button" className="btn small primary" disabled={archived || busy || session.conflict || !!applied} onClick={() => void append()}>{t(applied ? "research.appended" : inserted ? "research.retrySave" : "research.append")}</button>
              </> : !running && <button type="button" className="btn small primary" disabled={archived || busy || !config.data?.has_key || !preference.data} onClick={() => void start()}>{t("research.generate")}</button>}
            </>}
          </div>
        </footer>
      </section>
    </dialog>
  </div>;
}

function wasApplied(identity: string): boolean {
  try { return sessionStorage.getItem(`siye-research-applied:${identity}`) === "1"; } catch { return false; }
}
