import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Plus, X } from "lucide-react";
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

export default function SpaceResearchPanel({ spaceId, editor, session, archived, open, onClose }: {
  spaceId: number; editor: Editor; session: NoteSession; archived: boolean; open: boolean; onClose: () => void;
}) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const body = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1000px)").matches);
  const [question, setQuestion] = useState("");
  const [web, setWeb] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [job, setJob] = useState<api.ResearchJob | null>(null);
  const [turns, setTurns] = useState<api.ResearchJob[]>([]);
  const [initialized, setInitialized] = useState(false);
  const jobVersion = useRef(0);
  const inserted = job && session.hasResearch(job.job_id);
  const applied = job && (session.hasSavedResearch(job.job_id) || wasApplied(job.job_id));
  const config = useQuery({ queryKey: ["research-config"], queryFn: api.getResearchConfig, enabled: open, retry: false });
  const conversations = useQuery({ queryKey: ["research-conversations", spaceId], queryFn: () => api.listResearchConversations(spaceId), enabled: open, retry: false });
  const latest = useQuery({ queryKey: ["research-latest", spaceId], queryFn: async () => {
    const version = jobVersion.current;
    const next = await api.latestResearch(spaceId);
    const history = next ? await api.getResearchConversation(spaceId, next.conversation_id) : [];
    if (version === jobVersion.current) { setJob(next); setTurns(history); setInitialized(true); }
    return next;
  }, enabled: open && !initialized, retry: false, refetchOnWindowFocus: false });
  const preference = useQuery({ queryKey: ["research-web", spaceId], queryFn: () => api.getWebPreference(spaceId), enabled: open, retry: false });
  useEffect(() => { if (preference.data) setWeb(preference.data.web_enabled); }, [preference.data]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1000px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const element = dialog.current;
    if (!element || !open) return;
    if (narrow) element.showModal(); else element.show();
    return () => { element.close(); };
  }, [open, narrow]);
  const running = job?.status === "collecting" || job?.status === "analyzing";
  const locked = busy || running || job?.status === "awaiting_sources";
  useEffect(() => {
    if (!open || !running || !job) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const version = jobVersion.current;
      try {
        const next = await api.getResearch(job.job_id);
        if (!disposed && version === jobVersion.current) {
          setJob(next); setError("");
          if (!["collecting", "analyzing"].includes(next.status)) void conversations.refetch();
        }
      } catch (cause) { if (!disposed && version === jobVersion.current) setError(spaceError(cause)); }
      if (!disposed) timer = setTimeout(() => { void poll(); }, 1000);
    };
    timer = setTimeout(() => { void poll(); }, 600);
    return () => { disposed = true; clearTimeout(timer); };
  }, [open, running, job?.job_id]);
  useEffect(() => {
    if (open && job?.status === "ready") body.current?.scrollTo({ top: body.current.scrollHeight });
  }, [open, job?.job_id, job?.status]);
  const run = async (operation: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (cause) { setError(spaceError(cause)); }
    finally { pending.current = false; setBusy(false); }
  };
  const updateJob = async (request: () => Promise<api.ResearchJob>) => {
    const version = ++jobVersion.current;
    const next = await request();
    if (version !== jobVersion.current) return;
    setJob(next); setInitialized(true);
    setTurns((previous) => [...previous.filter((row) => row.job_id !== next.job_id && row.conversation_id === next.conversation_id).map((row) => row.job_id === job?.job_id ? job : row), next]);
    void conversations.refetch();
  };
  const start = () => run(async () => {
    const conversation = job?.status === "ready" ? job.conversation_id : undefined;
    await updateJob(() => api.createResearch(spaceId, question, web, conversation));
    setQuestion("");
  });
  const newConversation = () => {
    jobVersion.current += 1; setInitialized(true); setJob(null); setTurns([]); setQuestion(""); setError(""); setNotice("");
    dialog.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
  };
  const switchConversation = (id: string) => run(async () => {
    const version = ++jobVersion.current;
    const history = await api.getResearchConversation(spaceId, id);
    if (version !== jobVersion.current) return;
    setTurns(history); setJob(history[history.length - 1] ?? null); setInitialized(true); setQuestion("");
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
    if (!inserted && !session.appendResearch(job.job_id, job.document, (content) => editor.chain().insertContentAt(editor.state.doc.content.size, content).run())) throw new Error(t("research.noteConflict"));
    if (!await session.flush()) throw new Error(t("research.appendSaveFailed"));
    setNotice(t("research.appended"));
  });
  const progress = !job || job.status === "collecting" ? 0 : job.status === "awaiting_sources" ? 1 : job.status === "analyzing" ? 2 : job.status === "ready" ? 3 : -1;
  return createPortal(<dialog ref={dialog} className="space-research-dialog" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); } }}>
    <section className="space-research">
      <header className="space-research-head">
        <h2 id={titleId}>{t("research.title")}</h2>
        <div className="space-research-actions">
          <button type="button" className="btn small" disabled={!!locked || !initialized} onClick={newConversation}><Plus aria-hidden="true" />{t("research.newConversation")}</button>
          <button type="button" className="btn small" aria-label={t("research.close")} onClick={onClose}><X aria-hidden="true" /></button>
        </div>
      </header>
      <div className="space-research-history">
        <select aria-label={t("research.history")} value={job?.conversation_id ?? ""} disabled={!!locked || !initialized} onChange={(event) => { if (event.target.value) void switchConversation(event.target.value); else newConversation(); }}>
          <option value="">{t("research.newConversation")}</option>
          {conversations.data?.map((row) => <option key={row.id} value={row.id}>{row.title} · {row.turns}</option>)}
        </select>
        <p className="space-research-hint">{t("research.historyHint")}</p>
      </div>
      {job && <ol className="space-research-steps" aria-label={t("research.progress")}>
        {["collect", "review", "analyze", "result"].map((step, index) => <li key={step} className={index === progress ? "is-current" : index < progress ? "is-done" : ""} aria-current={index === progress ? "step" : undefined}><span>{index + 1}</span>{t(`research.step.${step}`)}</li>)}
      </ol>}
      <div className="space-research-body" ref={body}>
        {!config.data?.has_key && <p className="space-research-warning">{t("research.setupRequired")} <a href="#/settings/ai">{t("research.configure")}</a></p>}
        {!job && <p className="space-research-hint">{t("research.intro")}</p>}
        {turns.filter((row) => row.job_id !== job?.job_id).map((row) => <details className="space-research-turn" key={row.job_id}>
          <summary>{row.question || t("research.generate")}</summary>
          {row.document ? <div className="space-research-preview">{row.document.content?.map(previewNode)}</div> : <p>{t(`research.status.${row.status}`)}</p>}
        </details>)}
        {job && <div className="space-research-job">
          <div className="space-research-question">{job.question || t("research.generate")}</div>
          <div className="space-research-task"><strong role="status">{t(`research.status.${job.status}`)}</strong><span>{t(job.web_enabled ? "research.taskWebOn" : "research.taskWebOff")}</span></div>
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
            <button type="button" className="btn small" disabled={archived || busy || session.conflict || !!applied} onClick={() => void append()}>{t(applied ? "research.appended" : inserted ? "research.retrySave" : "research.append")}</button>
          </>}
        </div>}
      </div>
      <footer className="space-research-footer">
        {(error || config.error || latest.error || preference.error || conversations.error) && <p role="alert">{error || spaceError(config.error || latest.error || preference.error || conversations.error)}</p>}
        {notice && <p role="status">{notice}</p>}
        <label className="space-research-composer">{t("research.question")}<textarea rows={2} maxLength={2000} value={question} placeholder={t(job?.status === "ready" ? "research.followupHint" : "research.questionHint")} disabled={!!locked || archived} onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing && !locked && !archived && initialized && config.data?.has_key && preference.data && (question.trim() || job?.status !== "ready")) { event.preventDefault(); void start(); } }} /></label>
        <label className="space-research-web"><input type="checkbox" checked={web} disabled={busy || !preference.data} onChange={(event) => {
          const enabled = event.target.checked; setWeb(enabled);
          void run(async () => { try { await api.saveWebPreference(spaceId, enabled); } catch (cause) { setWeb(!enabled); throw cause; } });
        }} />{t("research.allowWeb")}</label>
        <p className="space-research-hint">{t(web ? "research.webHint" : "research.localHint")}</p>
        <div className="space-research-actions">
          {(running || job?.status === "awaiting_sources") && <button type="button" className="btn small" disabled={busy} onClick={() => void run(async () => { await updateJob(() => api.researchAction(job!.job_id, "cancel")); })}>{t("research.cancel")}</button>}
          {job && ["awaiting_sources", "failed"].includes(job.status) && <button type="button" className="btn small" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(job.job_id, "retry")); })}>{t("research.retrySources")}</button>}
          {job?.status === "awaiting_sources" ? <button type="button" className="btn small primary" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(job.job_id, "generate")); })}>{t("research.continue")}</button>
          : !running && <button type="button" className="btn small primary" disabled={archived || busy || !initialized || !config.data?.has_key || !preference.data || (job?.status === "ready" && !question.trim())} onClick={() => void start()}>{t(job?.status === "ready" ? "research.sendFollowup" : "research.generate")}</button>}
        </div>
      </footer>
    </section>
  </dialog>, document.body);
}

function wasApplied(identity: string): boolean {
  try { return sessionStorage.getItem(`siye-research-applied:${identity}`) === "1"; } catch { return false; }
}
