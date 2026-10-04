import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowUp, Check, ChevronDown, Globe, History, LoaderCircle, Maximize2, MessageSquare, Minimize2, Plus, Search, Sparkles, Square, Terminal, X } from "lucide-react";
import type { Editor } from "@tiptap/react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import * as api from "@/lib/researchApi";
import { sectionReading, transcriptDuration } from "@/lib/researchPresentation";
import { fetchSpace } from "@/lib/spacesApi";
import { spaceError, type NoteDocument, type NoteSession } from "@/lib/spaceNotes";

function previewNode(node: NoteDocument, index: number): ReactNode {
  const children = node.content?.map(previewNode);
  if (node.type === "text") {
    const link = node.marks?.find((mark) => mark.type === "link");
    const value = node.marks?.some((mark) => mark.type === "bold") ? <strong>{node.text}</strong> : node.text;
    return link ? <a key={index} href={String(link.attrs?.href)} target="_blank" rel="noopener noreferrer">{value}</a> : <span key={index}>{value}</span>;
  }
  if (node.type === "heading") return node.attrs?.level === 1 ? <h1 key={index}>{children}</h1> : node.attrs?.level === 2 ? <h2 key={index}>{children}</h2> : <h3 key={index}>{children}</h3>;
  if (node.type === "paragraph") return <p key={index}>{children}</p>;
  if (node.type === "bulletList") return <ul key={index}>{children}</ul>;
  if (node.type === "orderedList") return <ol key={index} start={Number(node.attrs?.start) || 1}>{children}</ol>;
  if (node.type === "listItem") return <li key={index}>{children}</li>;
  return <div key={index}>{children}</div>;
}

export default function SpaceResearchPanel({ spaceId, spaceName, sourceCount, editor, session, archived, open, onClose }: {
  spaceId: number; spaceName: string; sourceCount: number; editor: Editor; session: NoteSession; archived: boolean; open: boolean; onClose: () => void;
}) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const body = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1000px)").matches);
  const [width, setWidth] = useState(() => {
    try { return Math.max(360, Math.min(Number(localStorage.getItem("siye-research-width")) || 560, window.innerWidth)); } catch { return 560; }
  });
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [expanded, setExpanded] = useState(false);
  const drag = useRef<{ pointer: number; x: number; width: number; latest: number } | null>(null);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [sessionSearch, setSessionSearch] = useState("");
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
  const config = useQuery({ queryKey: ["research-config"], queryFn: api.getResearchConfig, enabled: open, retry: false });
  const conversations = useQuery({ queryKey: ["research-conversations", spaceId], queryFn: () => api.listResearchConversations(spaceId), enabled: open, retry: false });
  const latest = useQuery({ queryKey: ["research-latest", spaceId], queryFn: async () => {
    const version = jobVersion.current;
    const next = await api.latestResearch(spaceId);
    const history = next ? await api.getResearchConversation(spaceId, next.conversation_id) : [];
    if (version === jobVersion.current) { setJob(next); setTurns(history.slice(-10)); setInitialized(true); }
    return next;
  }, enabled: open && !initialized, retry: false, refetchOnWindowFocus: false });
  const preference = useQuery({ queryKey: ["research-web", spaceId], queryFn: () => api.getWebPreference(spaceId), enabled: open, retry: false });
  useEffect(() => { if (preference.data) setWeb(preference.data.web_enabled); }, [preference.data]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1000px)");
    const update = () => setNarrow(media.matches);
    const resize = () => setViewportWidth(window.innerWidth);
    media.addEventListener("change", update);
    window.addEventListener("resize", resize);
    return () => { media.removeEventListener("change", update); window.removeEventListener("resize", resize); };
  }, []);
  const minimumWidth = sessionsOpen ? 680 : 360;
  const panelWidth = narrow || expanded ? viewportWidth : Math.min(Math.max(width, minimumWidth), viewportWidth);
  const fullscreen = !narrow && panelWidth === viewportWidth;
  useEffect(() => {
    if (!open) return;
    document.body.style.setProperty("--research-panel-width", `${panelWidth}px`);
    document.body.classList.toggle("has-ai-expanded", fullscreen);
    return () => { document.body.style.removeProperty("--research-panel-width"); document.body.classList.remove("has-ai-expanded"); };
  }, [open, panelWidth, fullscreen]);
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
    setTurns((previous) => [...previous.filter((row) => row.job_id !== next.job_id && row.conversation_id === next.conversation_id).map((row) => row.job_id === job?.job_id ? job : row).slice(-9), next]);
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
    setTurns(history.slice(-10)); setJob(history[history.length - 1] ?? null); setInitialized(true); setQuestion("");
    if (narrow) setSessionsOpen(false);
  });
  const append = (result: api.ResearchJob) => run(async () => {
    if (!result.document || session.hasSavedResearch(result.job_id) || wasApplied(result.job_id)) return;
    const current = await fetchSpace(spaceId);
    if (current.archived) throw new Error(t("research.archived"));
    if (current.note_revision !== session.revision) {
      if (!session.sync(current.note_document, current.note_revision)) throw new Error(t("research.noteConflict"));
      editor.commands.setContent(session.document, { emitUpdate: false });
    }
    if (editor.view.composing) throw new Error(t("research.finishTyping"));
    if (!session.hasResearch(result.job_id) && !session.appendResearch(result.job_id, result.document, (content) => editor.chain().insertContentAt(editor.state.doc.content.size, content).run())) throw new Error(t("research.noteConflict"));
    if (!await session.flush()) throw new Error(t("research.appendSaveFailed"));
    setNotice(t("research.appended"));
  });
  const displayed = job ? [...turns.filter((row) => row.job_id !== job.job_id), job] : [];
  const send = () => { if (!pending.current) void start(); };
  const sendDisabled = (sourceCount === 0 && job?.status !== "ready") || archived || busy || !initialized || !config.data?.has_key || !preference.data || !question.trim();
  const cancel = () => run(async () => { if (job) await updateJob(() => api.researchAction(job.job_id, "cancel")); });
  const toggleExpanded = () => {
    if (fullscreen) {
      setExpanded(false);
      if (width >= viewportWidth) {
        setWidth(560);
        try { localStorage.setItem("siye-research-width", "560"); } catch { /* Storage may be unavailable. */ }
      }
    } else { setExpanded(true); setSessionsOpen(true); }
  };
  return createPortal(<dialog ref={dialog} className={`space-research-dialog${fullscreen ? " is-expanded" : ""}${sessionsOpen && !narrow ? " has-session-column" : ""}`} aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (sessionsOpen) setSessionsOpen(false); else onClose(); } }}>
    {!narrow && <div className="research-resize-handle" role="separator" tabIndex={0} aria-orientation="vertical" aria-label={t("research.resizePanel")} aria-valuemin={minimumWidth} aria-valuemax={viewportWidth} aria-valuenow={panelWidth}
      onPointerDown={(event) => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); drag.current = { pointer: event.pointerId, x: event.clientX, width: panelWidth, latest: panelWidth }; }}
      onPointerMove={(event) => { const start = drag.current; if (!start || start.pointer !== event.pointerId) return; start.latest = Math.max(minimumWidth, Math.min(start.width + start.x - event.clientX, viewportWidth)); setExpanded(false); setWidth(start.latest); }}
      onPointerUp={() => { if (drag.current) { try { localStorage.setItem("siye-research-width", String(drag.current.latest)); } catch { /* Storage may be unavailable. */ } drag.current = null; } }}
      onLostPointerCapture={() => { drag.current = null; }}
      onKeyDown={(event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; event.preventDefault(); const next = event.key === "Home" ? minimumWidth : event.key === "End" ? viewportWidth : Math.max(minimumWidth, Math.min(panelWidth + (event.key === "ArrowLeft" ? 24 : -24), viewportWidth)); setExpanded(false); setWidth(next); try { localStorage.setItem("siye-research-width", String(next)); } catch { /* Storage may be unavailable. */ } }} />}
    <section className="space-research">
      <header className="space-research-head">
        <div className="research-chat-title"><Sparkles aria-hidden="true" /><h2 id={titleId}>{t("research.title")}</h2></div>
        <div className="space-research-actions">
          <button type="button" className="research-icon-button" title={t("research.newConversation")} aria-label={t("research.newConversation")} disabled={!!locked || !initialized} onClick={newConversation}><Plus aria-hidden="true" /></button>
          <button type="button" className="research-icon-button" title={t("research.history")} aria-label={t("research.history")} aria-expanded={sessionsOpen} onClick={() => setSessionsOpen(!sessionsOpen)}><History aria-hidden="true" /></button>
          {!narrow && <button type="button" className="research-icon-button" title={t(fullscreen ? "research.collapsePanel" : "research.expandPanel")} aria-label={t(fullscreen ? "research.collapsePanel" : "research.expandPanel")} aria-expanded={fullscreen} onClick={toggleExpanded}>{fullscreen ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}</button>}
          <button type="button" className="research-icon-button" aria-label={t("research.close")} onClick={onClose}><X aria-hidden="true" /></button>
        </div>
      </header>
      <div className="research-chat-context"><span>{spaceName}</span><span>{t("research.selectedSources", { count: sourceCount })}</span></div>
      <div className="space-research-body" ref={body}>
        {config.data && !config.data.has_key && <p className="space-research-warning">{t("research.setupRequired")} <a href="#/settings/ai">{t("research.configure")}</a></p>}
        {!job && <div className="research-chat-empty"><Sparkles aria-hidden="true" /><h3>{t("research.chatWelcome")}</h3><p>{t(sourceCount === 0 ? "research.addSourcesFirst" : "research.chatIntro")}</p>
          <div className="research-starters">{["starterSummary", "starterCompare", "starterGaps"].map((key) => <button type="button" key={key} disabled={archived || sourceCount === 0} onClick={() => { setQuestion(t(`research.${key}`)); dialog.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus(); }}>{t(`research.${key}`)}</button>)}</div>
        </div>}
        {displayed.map((row) => {
          const current = row.job_id === job?.job_id;
          const rowInserted = session.hasResearch(row.job_id);
          const rowApplied = session.hasSavedResearch(row.job_id) || wasApplied(row.job_id);
          return <article className="research-chat-turn" key={row.job_id}>
            <div className="research-user-message">{row.question || t("research.defaultQuestion")}</div>
            <div className="research-assistant-message">
              <div className="research-message-label"><Sparkles aria-hidden="true" /><span>{t("research.assistantName")}</span><small>{t(row.web_enabled ? "research.webUsed" : "research.spaceUsed")}</small></div>
              {!row.document && <p className="research-chat-status" role="status">{t(`research.status.${row.status}`)}{row.status === "collecting" && ` · ${row.materials.length} / ${row.total_materials}`}</p>}
              {row.status === "collecting" && row.phase === "transcribing" && <p className="space-research-hint" role="status">{row.message}</p>}
              {row.error && <p role="alert">{row.error}</p>}
              {row.stale_snapshot && <p className="space-research-warning">{t("research.stale")}</p>}
              {!!row.activity?.length && <div className="research-execution" aria-label={t("research.toolProcess")}>
                {row.activity.map((event, index) => event.kind === "commentary" ? <div className="research-process-commentary" key={event.id || index}><small>{t("research.processCommentary")}</small><p>{event.message}</p></div> : <details className={`research-tool-step is-${event.status || (event.message.includes("未完成") ? "failed" : "completed")}`} key={event.id || index}>
                  <summary><ChevronDown aria-hidden="true" /><Terminal aria-hidden="true" /><strong>{event.tool.replace("mcp__research__", "")}</strong><span>{event.message !== event.tool ? event.message : event.summary}</span>{event.status === "running" && ["failed", "cancelled"].includes(row.status) ? <X aria-label={t("research.toolState.cancelled")} /> : event.status === "running" ? <LoaderCircle className="research-tool-spinner" aria-label={t("research.toolState.running")} /> : event.status === "failed" || event.status === "cancelled" ? <X aria-label={t(`research.toolState.${event.status}`)} /> : <Check aria-label={t("research.toolState.completed")} />}</summary>
                  <div className="research-tool-result"><span>{t(`research.toolState.${event.status === "running" && ["failed", "cancelled"].includes(row.status) ? "cancelled" : event.status || "completed"}`)}</span><p>{event.summary || event.message}</p></div>
                </details>)}
              </div>}
              {row.materials.length > 0 && <details className="research-tool-trace" open={row.status === "awaiting_sources"}>
                <summary><ChevronDown aria-hidden="true" />{t("research.readingRecord")}<span>{row.activity?.length || row.materials.length}</span></summary>
                {row.materials.length > 0 && <details className="research-material-details" open={row.status === "awaiting_sources"}><summary>{t("research.coverage")} · {row.materials.length}</summary>
                  <div className="space-research-materials">{row.materials.map((item) => <article key={item.key}>
                    <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
                    <div className="space-research-tags">{(["body", "comments", "subtitles"] as const).map((name) => <span key={name} className={`space-research-tag is-${item[name].state}`}>
                      {t(`research.component.${name}`)} {item[name].state === "ok" ? "✓" : item[name].state === "not_applicable" ? "·" : "×"} {t(`research.state.${item[name].state}`)}{name === "comments" && ` · ${t("research.commentCount", { count: item[name].count })}`}
                      {name === "subtitles" && item[name].metadata?.source && ` · ${transcriptDuration(item[name].metadata?.duration)} · ${t(`research.subtitleSource.${item[name].metadata?.source}`)}`}
                      {(item[name].collection_truncated ?? item[name].truncated) && ` · ${t("research.collectionPartial")}`}
                    </span>)}</div>
                    {(["body", "comments", "subtitles"] as const).map((name) => item[name].reason && <p key={name} className="space-research-hint">{t(`research.component.${name}`)}：{item[name].reason}</p>)}
                    {item.comments.sort && <p className="space-research-hint">{item.comments.sort}</p>}
                    {row.coverage.find((source) => source.key === item.key)?.sections && <p className="space-research-hint">{(["body", "comments", "subtitles"] as const).map((name) => {
                      const section = row.coverage.find((source) => source.key === item.key)!.sections![name];
                      return `${t(`research.component.${name}`)} ${t(sectionReading(section, name === "comments"), { read: section.read_chunks, total: section.chunks, count: section.count })}`;
                    }).join(" · ")}</p>}
                  </article>)}</div>
                </details>}
              </details>}
              {current && row.status === "awaiting_sources" && <div className="research-source-review"><p>{t("research.reviewChatHint")}</p><div className="space-research-actions">
                <button type="button" className="btn small primary" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(row.job_id, "generate")); })}>{t("research.continue")}</button>
                <button type="button" className="btn small" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(row.job_id, "retry")); })}>{t("research.retrySources")}</button>
              </div></div>}
              {row.document && <>
                <div className="space-research-preview" aria-label={t("research.preview")}>{row.document.content?.slice(2).map(previewNode)}</div>
                {row.coverage.some((item) => !item.complete) && <p className="space-research-warning">{t("research.unread")}</p>}
                {row.web_errors.length > 0 && <p className="space-research-hint">{t("research.webFailed")}</p>}
                {rowInserted && !rowApplied && <p className="space-research-warning">{t("research.draftInserted")}</p>}
                <div className="research-reply-actions"><button type="button" className="btn small" disabled={archived || busy || session.conflict || rowApplied} onClick={() => void append(row)}>{t(rowApplied ? "research.appended" : rowInserted ? "research.retrySave" : "research.append")}</button><span>{t(row.web_enabled ? "research.webUsed" : "research.spaceUsed")}</span></div>
              </>}
              {current && row.status === "failed" && <button type="button" className="btn small" disabled={busy || archived} onClick={() => void run(async () => { await updateJob(() => api.researchAction(row.job_id, "retry")); })}>{t("research.retrySources")}</button>}
            </div>
          </article>;
        })}
      </div>
      <div className="space-research-footer">
        {(error || config.error || latest.error || preference.error || conversations.error) && <p role="alert">{error || spaceError(config.error || latest.error || preference.error || conversations.error)}</p>}
        {notice && <p role="status">{notice}</p>}
        <div className="research-input-card">
          <textarea aria-label={t("research.messageInput")} rows={3} maxLength={2000} value={question} placeholder={t("research.messagePlaceholder")} disabled={!!locked || archived} onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing && !locked && !sendDisabled) { event.preventDefault(); send(); } }} />
          <div className="research-input-tools"><span className="research-model-label" title={config.data?.model}>{config.data?.model || t("research.noModel")}</span>
            <label className="research-web-toggle" title={t(web ? "research.webHint" : "research.localHint")}><input type="checkbox" aria-label={t("research.allowWeb")} checked={web} disabled={busy || !preference.data} onChange={(event) => {
              const enabled = event.target.checked; setWeb(enabled);
              void run(async () => { try { await api.saveWebPreference(spaceId, enabled); } catch (cause) { setWeb(!enabled); throw cause; } });
            }} /><Globe aria-hidden="true" /><span>{t("research.webShort")}</span></label>
            {running || job?.status === "awaiting_sources" ? <button type="button" className="research-send" aria-label={t("research.cancel")} title={t("research.cancel")} disabled={busy} onClick={() => void cancel()}><Square aria-hidden="true" /></button>
              : <button type="button" className="research-send" aria-label={t("research.send")} title={t("research.send")} disabled={sendDisabled} onClick={send}><ArrowUp aria-hidden="true" /></button>}
          </div>
        </div>
        <p className="research-input-hint">{t("research.sendShortcut")}</p>
      </div>
    </section>
      {sessionsOpen && <aside className="research-sessions" aria-label={t("research.history")}>
        <div className="research-sessions-head"><h3>{t("research.history")}</h3><button type="button" className="research-icon-button" aria-label={t("research.closeHistory")} onClick={() => setSessionsOpen(false)}><X aria-hidden="true" /></button></div>
        <button type="button" className="research-session-new" disabled={!!locked || !initialized} onClick={newConversation}><Plus aria-hidden="true" />{t("research.newConversation")}</button>
        <label className="research-session-search"><Search aria-hidden="true" /><input aria-label={t("research.searchHistory")} value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} placeholder={t("research.searchHistory")} /></label>
        <div className="research-session-list">{conversations.data?.filter((row) => row.title.toLocaleLowerCase().includes(sessionSearch.toLocaleLowerCase())).map((row) => <button type="button" key={row.id} className={job?.conversation_id === row.id ? "is-active" : ""} aria-pressed={job?.conversation_id === row.id} disabled={!!locked || !initialized} onClick={() => void switchConversation(row.id)}><MessageSquare aria-hidden="true" /><span>{row.title}<small>{t("research.conversationTurns", { count: row.turns })}</small></span></button>)}</div>
        <p className="space-research-hint">{t("research.historyHint")}</p>
      </aside>}
  </dialog>, document.body);
}

function wasApplied(identity: string): boolean {
  try { return sessionStorage.getItem(`siye-research-applied:${identity}`) === "1"; } catch { return false; }
}
