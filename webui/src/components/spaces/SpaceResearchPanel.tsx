import { useEffect, useRef, useState, type ReactNode } from "react";
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
  if (node.type === "heading") return <h3 key={index}>{children}</h3>;
  if (node.type === "paragraph") return <p key={index}>{children}</p>;
  return <div key={index}>{children}</div>;
}

export default function SpaceResearchPanel({ spaceId, editor, session, archived }: {
  spaceId: number; editor: Editor; session: NoteSession; archived: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
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
  const applied = job && (session.hasResearch(job.job_id) || wasApplied(job.job_id));
  const config = useQuery({ queryKey: ["research-config"], queryFn: api.getResearchConfig, enabled: open, retry: false });
  const latest = useQuery({ queryKey: ["research-latest", spaceId], queryFn: () => api.latestResearch(spaceId), enabled: open, retry: false });
  const preference = useQuery({ queryKey: ["research-web", spaceId], queryFn: () => api.getWebPreference(spaceId), enabled: open, retry: false });
  useEffect(() => { if (config.data) { setBaseUrl(config.data.base_url); setModel(config.data.model); } }, [config.data]);
  useEffect(() => { if (preference.data) setWeb(preference.data.web_enabled); }, [preference.data]);
  useEffect(() => { if (latest.data) setJob(latest.data); }, [latest.data]);
  const running = job?.status === "collecting" || job?.status === "analyzing";
  useEffect(() => {
    if (!open || !running || !job) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api.getResearch(job.job_id);
        if (!disposed) { setJob(next); setError(""); }
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
    if (job && !["ready", "failed", "cancelled"].includes(job.status)) await api.researchAction(job.job_id, "cancel");
    setJob(await api.createResearch(spaceId, question, web));
  });
  const append = () => run(async () => {
    if (!job?.document || applied) return;
    const current = await fetchSpace(spaceId);
    if (current.archived) throw new Error(t("research.archived"));
    if (current.note_revision !== session.revision) {
      if (!session.sync(current.note_document, current.note_revision)) throw new Error(t("research.noteConflict"));
      editor.commands.setContent(session.document, { emitUpdate: false });
    }
    if (editor.view.composing) throw new Error(t("research.finishTyping"));
    if (session.appendResearch(job.job_id, job.document, (content) => editor.chain().focus().insertContentAt(editor.state.doc.content.size, content).run())) {
      try { sessionStorage.setItem(`siye-research-applied:${job.job_id}`, "1"); } catch { /* The in-memory session still prevents duplicate clicks. */ }
      await session.flush();
      setNotice(t("research.appended"));
    } else throw new Error(t("research.noteConflict"));
  });

  return <details className="space-research" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{t("research.title")}</summary>
    <div className="space-research-body">
      <p className="space-research-hint">{t("research.intro")}</p>
      <details className="space-research-config"><summary>{t("research.configure")}</summary>
        {config.data && <p>{t(config.data.runtime.sdk_available && config.data.runtime.cli_available ? "research.runtimeReady" : "research.runtimeMissing")}</p>}
        <label>{t("research.address")}<input type="url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={busy || running} /></label>
        <label>{t("research.model")}<input value={model} onChange={(event) => setModel(event.target.value)} disabled={busy || running} /></label>
        <label>{t("research.key")}<input type="password" autoComplete="off" value={key} placeholder={config.data?.key_mask ?? ""} onChange={(event) => setKey(event.target.value)} disabled={busy || running} /></label>
        <p className="space-research-hint">{t("research.keyHint")}</p>
        <div className="space-research-actions">
          <button type="button" className="btn small" disabled={busy || running} onClick={() => void run(async () => {
            await api.saveResearchConfig(baseUrl, model, key || undefined); setKey(""); await config.refetch(); setNotice(t("research.configSaved"));
          })}>{t("research.saveConfig")}</button>
          <button type="button" className="btn small" disabled={busy || running || !config.data?.has_key} onClick={() => void run(async () => {
            await api.testResearchConnection(web); setNotice(t(web ? "research.webTestPassed" : "research.testPassed"));
          })}>{t("research.test")}</button>
          <button type="button" className="btn small" disabled={busy || running || !config.data?.has_key} onClick={() => void run(async () => {
            await api.deleteResearchConfig(); setKey(""); await config.refetch();
          })}>{t("research.clearConfig")}</button>
        </div>
      </details>
      <label>{t("research.question")}<textarea rows={2} maxLength={2000} value={question} placeholder={t("research.questionHint")} disabled={busy} onChange={(event) => setQuestion(event.target.value)} /></label>
      <label className="space-research-web"><input type="checkbox" checked={web} disabled={busy || !preference.data} onChange={(event) => {
        const enabled = event.target.checked;
        setWeb(enabled);
        void run(async () => {
          try { await api.saveWebPreference(spaceId, enabled); }
          catch (cause) { setWeb(!enabled); throw cause; }
        });
      }} />{t("research.allowWeb")}</label>
      <p className="space-research-hint">{t(web ? "research.webHint" : "research.localHint")}</p>
      <button type="button" className="btn small accent" disabled={archived || busy || running || !config.data?.has_key || !preference.data} onClick={() => void start()}>{t("research.generate")}</button>
      {(error || config.error || latest.error || preference.error) && <p role="alert">{error || spaceError(config.error || latest.error || preference.error)}</p>}
      {notice && <p role="status">{notice}</p>}
      {job && <div className="space-research-job">
        <p role="status">{t(`research.status.${job.status}`)}{running && ` · ${job.materials.length}`}</p>
        <p className="space-research-hint">{t(job.web_enabled ? "research.taskWebOn" : "research.taskWebOff")}</p>
        {job.error && <p role="alert">{job.error}</p>}
        {job.stale_snapshot && <p>{t("research.stale")}</p>}
        {job.materials.length > 0 && <details open={job.status === "awaiting_sources"}><summary>{t("research.coverage")}</summary>
          <div className="space-research-materials">{job.materials.map((item) => <div key={item.key}>
            <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
            {(["body", "comments", "subtitles"] as const).map((name) => <p key={name}>
              {t(`research.component.${name}`)}：{t(`research.state.${item[name].state}`)}
              {name === "comments" && ` · ${item.comments.entries.length} · ${item.comments.sort ?? ""}`}
              {item[name].truncated && ` · ${t("research.partial")}`}
              {item[name].reason && ` · ${item[name].reason}`}
            </p>)}
          </div>)}</div>
        </details>}
        {job.status === "failed" && <button type="button" className="btn small" disabled={busy || archived} onClick={() => void run(async () => { setJob(await api.researchAction(job.job_id, "retry")); })}>{t("research.retrySources")}</button>}
        {job.status === "awaiting_sources" && <>
          <p className="space-research-hint">{t("research.reviewHint")}</p>
          <div className="space-research-actions">
            <button type="button" className="btn small" disabled={busy || archived} onClick={() => void run(async () => { setJob(await api.researchAction(job.job_id, "retry")); })}>{t("research.retrySources")}</button>
            <button type="button" className="btn small accent" disabled={busy || archived} onClick={() => void run(async () => { setJob(await api.researchAction(job.job_id, "generate")); })}>{t("research.continue")}</button>
          </div>
        </>}
        {(running || job.status === "awaiting_sources") && <button type="button" className="btn small" disabled={busy} onClick={() => void run(async () => { setJob(await api.researchAction(job.job_id, "cancel")); })}>{t("research.cancel")}</button>}
        {job.document && <>
          <div className="space-research-preview" aria-label={t("research.preview")}>{job.document.content?.map(previewNode)}</div>
          {job.coverage.some((row) => !row.complete) && <p>{t("research.unread")}</p>}
          {job.web_errors.length > 0 && <p>{t("research.webFailed")}</p>}
          {job.cost_usd !== null && <p>{t("research.cost", { cost: job.cost_usd.toFixed(4) })}</p>}
          <button type="button" className="btn small accent" disabled={archived || busy || session.conflict || !!applied} onClick={() => void append()}>{t(applied ? "research.appended" : "research.append")}</button>
        </>}
      </div>}
    </div>
  </details>;
}

function wasApplied(identity: string): boolean {
  try { return sessionStorage.getItem(`siye-research-applied:${identity}`) === "1"; } catch { return false; }
}
