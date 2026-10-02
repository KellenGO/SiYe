import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { FontSize, TextStyle } from "@tiptap/extension-text-style";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { Fragment, Slice, type Node as EditorNode } from "@tiptap/pm/model";
import { Bold, Italic, Underline, Undo2, Redo2, List, ListOrdered, ListChecks, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSpaceDetail, useSpaces } from "@/hooks/useSpaces";
import { EMPTY_NOTE, NOTE_FONT_SIZES, noteText, spaceError, type NoteDocument, type NoteSession } from "@/lib/spaceNotes";
import { fetchSpace } from "@/lib/spacesApi";
import SpaceResearchPanel from "./SpaceResearchPanel";

function cleanPastedNode(node: EditorNode): EditorNode {
  const marks = node.marks.filter((mark) => mark.type.name !== "textStyle" || NOTE_FONT_SIZES.some((size) => mark.attrs.fontSize === `${size}px`));
  if (node.isText) return node.mark(marks);
  const children: EditorNode[] = [];
  node.content.forEach((child) => children.push(cleanPastedNode(child)));
  return node.copy(Fragment.fromArray(children)).mark(marks);
}


function NotePanel({ editor, session, name, spaceId, archived, modal, onClose }: { editor: Editor; session: NoteSession; name: string; spaceId: number; archived: boolean; modal: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const [, updateToolbar] = useState(0);
  const [latest, setLatest] = useState<{ document: NoteDocument; revision: number } | null>(null);
  const [latestError, setLatestError] = useState("");
  const status = archived ? "spaces.readOnly" : session.saving ? "spaces.saving" : session.error ? "spaces.saveFailed" : session.dirty ? "spaces.unsaved" : "spaces.saved";
  useEffect(() => {
    const update = () => updateToolbar((value) => value + 1);
    editor.on("transaction", update);
    return () => { editor.off("transaction", update); };
  }, [editor]);
  const action = (label: string, icon: ReactNode, active: boolean, apply: () => void, disabled = false) => <button type="button" title={t(label)} aria-label={t(label)} aria-pressed={active} disabled={archived || disabled} onMouseDown={(event) => event.preventDefault()} onClick={apply}>{icon}</button>;
  return <aside className="space-note-panel" aria-label={t("spaces.note")} role={modal ? "dialog" : undefined} aria-modal={modal || undefined}>
    <div className="space-note-head"><div><span className="eyebrow">{t("spaces.note")}</span><h2>{name}</h2></div><button type="button" className="btn small" aria-label={t("spaces.closeNote")} onClick={onClose}><X aria-hidden="true" /></button></div>
    <div className="space-note-toolbar" role="toolbar" aria-label={t("spaces.formatting")}>
      <select aria-label={t("spaces.paragraphStyle")} disabled={archived} value={[1, 2, 3].find((level) => editor.isActive("heading", { level })) ?? 0} onChange={(event) => {
        const level = Number(event.target.value);
        if (level === 0) editor.chain().focus().setParagraph().run();
        else editor.chain().focus().setHeading({ level: level as 1 | 2 | 3 }).run();
      }}><option value={0}>{t("spaces.body")}</option>{[1, 2, 3].map((level) => <option key={level} value={level}>{t("spaces.heading", { level })}</option>)}</select>
      <select aria-label={t("spaces.fontSize")} disabled={archived} value={editor.getAttributes("textStyle").fontSize ?? "16px"} onChange={(event) => editor.chain().focus().setFontSize(event.target.value).run()}>{NOTE_FONT_SIZES.map((size) => <option key={size} value={`${size}px`}>{size}</option>)}</select>
      {action("spaces.bold", <Bold />, editor.isActive("bold"), () => { editor.chain().focus().toggleBold().run(); })}
      {action("spaces.italic", <Italic />, editor.isActive("italic"), () => { editor.chain().focus().toggleItalic().run(); })}
      {action("spaces.underline", <Underline />, editor.isActive("underline"), () => { editor.chain().focus().toggleUnderline().run(); })}
      {action("spaces.bullets", <List />, editor.isActive("bulletList"), () => { editor.chain().focus().toggleBulletList().run(); })}
      {action("spaces.numbered", <ListOrdered />, editor.isActive("orderedList"), () => { editor.chain().focus().toggleOrderedList().run(); })}
      {action("spaces.checklist", <ListChecks />, editor.isActive("taskList"), () => { editor.chain().focus().toggleTaskList().run(); })}
      {action("spaces.undo", <Undo2 />, false, () => { editor.chain().focus().undo().run(); }, !editor.can().undo())}
      {action("spaces.redo", <Redo2 />, false, () => { editor.chain().focus().redo().run(); }, !editor.can().redo())}
    </div>
    <EditorContent editor={editor} className="space-note-content" onCompositionEnd={() => { session.change(editor.getJSON() as NoteDocument); }} onBlur={() => { void session.flush(); }} />
    <div className="space-note-status" role="status"><span>{t(status)}</span><span>{Array.from(noteText(session.document).replace(/\n/g, "")).length.toLocaleString()} / 50,000</span></div>
    {session.error && <div className="space-note-error" role="alert"><p>{session.error}</p>{!session.conflict && <button type="button" className="btn small" onClick={() => void session.flush()}>{t("spaces.retry")}</button>}
      {session.conflict && <button type="button" className="btn small" onClick={async () => {
        try { const detail = await fetchSpace(spaceId); setLatest({ document: detail.note_document, revision: detail.note_revision }); setLatestError(""); }
        catch (error) { setLatestError(spaceError(error)); }
      }}>{t("spaces.showLatest")}</button>}
      {latestError && <p>{latestError}</p>}
      {latest && <div className="space-note-conflict"><h3>{t("spaces.latestNote")}</h3><pre>{noteText(latest.document)}</pre><button type="button" className="btn small" disabled={archived} onClick={() => {
        if (window.confirm(t("spaces.replaceLatestConfirm"))) { session.resolveWithRevision(latest.revision); setLatest(null); void session.flush(); }
      }}>{t("spaces.keepDraft")}</button></div>}
    </div>}
  </aside>;
}

export default function SpaceNoteController({ spaceId, open, onClose, researchOpen, onCloseResearch, target }: { spaceId: number; open: boolean; onClose: () => void; researchOpen: boolean; onCloseResearch: () => void; target: HTMLDivElement | null }) {
  const { t } = useTranslation();
  const spaces = useSpaces();
  const detail = useSpaceDetail(spaceId);
  const session = detail.data ? spaces.session(detail.data) : undefined;
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const [container] = useState(() => document.createElement("div"));
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1000px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1000px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const modal = Boolean(narrow && open && target && !target.closest(".local-content-research-layout"));
  useLayoutEffect(() => {
    container.style.display = open ? "" : "none";
    container.className = modal ? "space-note-floating" : "";
    if (target && open) (modal ? document.body : target).appendChild(container);
    return () => { container.remove(); };
  }, [container, open, target, modal]);
  const editor = useEditor({
    extensions: [StarterKit.configure({ heading: { levels: [1, 2, 3] }, blockquote: false, code: false, codeBlock: false, horizontalRule: false, strike: false,
      link: { openOnClick: false, protocols: ["http", "https"] }, orderedList: { keepMarks: true } }), TextStyle, FontSize, TaskList, TaskItem.configure({ nested: true })],
    content: session?.document ?? EMPTY_NOTE,
    editable: Boolean(session) && !detail.data?.archived,
    editorProps: {
      attributes: { role: "textbox", "aria-label": t("spaces.noteEditor"), "aria-multiline": "true", "data-space-id": String(spaceId) },
      handlePaste: (_view, event) => { if (event.clipboardData?.files.length) { event.preventDefault(); return true; } return false; },
      handleDrop: (_view, event) => { if (event.dataTransfer?.files.length) { event.preventDefault(); return true; } return false; },
      transformPasted: (slice) => {
        const children: EditorNode[] = [];
        slice.content.forEach((node) => children.push(cleanPastedNode(node)));
        return new Slice(Fragment.fromArray(children), slice.openStart, slice.openEnd);
      },
    },
    onUpdate: ({ editor: current }) => { sessionRef.current?.change(current.getJSON() as NoteDocument, current.view.composing); },
  }, [spaceId, Boolean(session)]);
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    const first = () => container.querySelector<HTMLElement>("button:not(:disabled)");
    first()?.focus();
    const controls = () => [...container.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), input:not(:disabled), textarea:not(:disabled), summary, a[href], [contenteditable="true"]')].filter((control) => control.getClientRects().length > 0 && control.tabIndex >= 0);
    const keyboard = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest("dialog[open]")) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
      if (event.key === "Tab") {
        const all = controls();
        const end = event.shiftKey ? all[all.length - 1] : all[0];
        if (document.activeElement === (event.shiftKey ? all[0] : all[all.length - 1])) { event.preventDefault(); end?.focus(); }
      }
    };
    const focus = (event: FocusEvent) => { if (!(event.target as HTMLElement).closest(".space-research-dialog[open]") && !container.contains(event.target as Node)) first()?.focus(); };
    const outside = (event: PointerEvent) => { if (event.target === container) onClose(); };
    container.addEventListener("keydown", keyboard);
    container.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", focus);
    return () => {
      container.removeEventListener("keydown", keyboard);
      container.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", focus);
      if (previous?.isConnected) previous.focus();
    };
  }, [container, modal, onClose, Boolean(editor)]);
  useEffect(() => { editor?.setEditable(Boolean(session) && !detail.data?.archived, false); }, [editor, session, detail.data?.archived]);
  useEffect(() => {
    if (session && detail.data && session.sync(detail.data.note_document, detail.data.note_revision)) editor?.commands.setContent(session.document, { emitUpdate: false });
  }, [editor, session, detail.data]);
  return editor && session && detail.data ? <>
    {createPortal(<NotePanel key={spaceId} editor={editor} session={session} name={detail.data.name} spaceId={detail.data.id} archived={detail.data.archived} modal={modal} onClose={onClose} />, container)}
    <SpaceResearchPanel key={spaceId} spaceId={spaceId} editor={editor} session={session} archived={detail.data.archived} open={researchOpen} onClose={onCloseResearch} />
  </> : null;
}
