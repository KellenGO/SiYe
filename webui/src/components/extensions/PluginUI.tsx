import * as React from "react";
import * as dom from "react-dom";
import * as jsx from "react/jsx-runtime";
import * as i18n from "react-i18next";
import * as query from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useEffect, useState, type ComponentType } from "react";
import type { Editor } from "@tiptap/react";
import { fetchSpace } from "@/lib/spacesApi";
import { spaceError, type NoteDocument, type NoteSession } from "@/lib/spaceNotes";
import type { WorkspaceSplitLayout } from "@/components/spaces/WorkspaceContext";
import { useExtensions } from "@/hooks/useExtensions";

interface NoteBridge { conflict: boolean; hasResearch(id: string): boolean; hasSavedResearch(id: string): boolean; append(id: string, document: NoteDocument): Promise<void>; }
interface PanelProps { spaceId: number; spaceName: string; sourceCount: number; note: NoteBridge; archived: boolean; open: boolean; onClose(): void; split: WorkspaceSplitLayout; }
interface PluginModule { apiVersion: number; Panel: ComponentType<PanelProps>; Settings: ComponentType; }
declare global { interface Window { SiYeAI?: PluginModule; SiYeAIHost?: unknown; } }
let loading: Promise<PluginModule> | null = null;
let loadedVersion = "";
function loadPlugin(version: string): Promise<PluginModule> {
  if (loading && loadedVersion === version) return loading;
  loadedVersion = version;
  window.SiYeAIHost = { react: React, dom, jsx, i18n, query, bridge: { spaceError } };
  loading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `/api/extensions/ai/assets/ui.js?v=${encodeURIComponent(version)}`;
    script.onload = () => {
      script.remove();
      if (window.SiYeAI?.apiVersion === 1) resolve(window.SiYeAI);
      else { loading = null; reject(new Error("扩展界面与当前软件不兼容")); }
    };
    script.onerror = () => { script.remove(); loading = null; reject(new Error("扩展界面未能加载，请重试")); };
    document.head.append(script);
  });
  return loading;
}
function usePlugin(active: boolean) {
  const extension = useExtensions();
  const [module, setModule] = useState<PluginModule | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let disposed = false;
    if (!extension.data?.enabled) { setModule(null); return; }
    if (!active && !module) return;
    setError("");
    void loadPlugin(extension.data.version!).then((next) => { if (!disposed) setModule(next); }).catch((cause) => { if (!disposed) setError(spaceError(cause)); });
    return () => { disposed = true; };
  }, [active, extension.data?.enabled, extension.data?.version, attempt]);
  return { module: extension.data?.enabled ? module : null, error, retry: () => setAttempt((value) => value + 1) };
}
export function PluginSettings() {
  const { module, error, retry } = usePlugin(true);
  return module ? <module.Settings /> : <p role={error ? "alert" : "status"}>{error || "正在加载 AI 配置…"}{error && <button className="text-link" onClick={retry}>重试</button>}</p>;
}
export function PluginPanel(props: Omit<PanelProps, "note"> & { editor: Editor; session: NoteSession }) {
  const { module, error, retry } = usePlugin(props.open);
  const { t } = useTranslation();
  const note: NoteBridge = {
    conflict: props.session.conflict,
    hasResearch: (id) => props.session.hasResearch(id),
    hasSavedResearch: (id) => props.session.hasSavedResearch(id),
    append: async (id, document) => {
      if (props.session.hasSavedResearch(id)) return;
      const current = await fetchSpace(props.spaceId);
      if (current.archived) throw new Error(t("research.archived"));
      if (current.note_revision !== props.session.revision) {
        if (!props.session.sync(current.note_document, current.note_revision)) throw new Error(t("research.noteConflict"));
        props.editor.commands.setContent(props.session.document, { emitUpdate: false });
      }
      if (props.editor.view.composing) throw new Error(t("research.finishTyping"));
      if (!props.session.hasResearch(id) && !props.session.appendResearch(id, document, (content) => props.editor.chain().insertContentAt(props.editor.state.doc.content.size, content).run())) throw new Error(t("research.noteConflict"));
      if (!await props.session.flush()) throw new Error(t("research.appendSaveFailed"));
    }
  };
  if (module) return <module.Panel {...props} note={note} />;
  return props.open ? <div className="extension-loading" role={error ? "alert" : "status"}>{error || "正在加载 AI 助手…"}{error && <button className="text-link" onClick={retry}>重试</button>}<button className="text-link" onClick={props.onClose}>关闭</button></div> : null;
}
