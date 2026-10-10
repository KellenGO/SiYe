import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, LoaderCircle, Plug, RefreshCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useExtensions } from "@/hooks/useExtensions";
import * as api from "@/lib/extensionsApi";
import { spaceError } from "@/lib/spaceNotes";
import { PluginSettings } from "@/components/extensions/PluginUI";
import "./ExtensionsPage.css";

export function ExtensionsPage() {
  const query = useExtensions();
  const client = useQueryClient();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [details, setDetails] = useState(false);
  const [uninstallOpen, setUninstallOpen] = useState(false);
  const [clearData, setClearData] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const detailsButton = useRef<HTMLButtonElement>(null);
  const row = query.data;
  const busy = pending || Boolean(row && ["downloading", "installing", "stopping", "uninstalling"].includes(row.phase));
  const run = async (action: () => Promise<api.ExtensionStatus>) => {
    if (pending) return false;
    setPending(true);
    setError("");
    try {
      const next = await action();
      client.setQueryData(["extensions-ai"], next);
      client.removeQueries({ predicate: (item) => String(item.queryKey[0]).startsWith("research-") });
      return true;
    } catch (cause) {
      setError(spaceError(cause));
      return false;
    } finally {
      setPending(false);
    }
  };
  useEffect(() => {
    void api.extensionCatalog().then((next) => client.setQueryData(["extensions-ai"], next)).catch(() => {});
  }, [client]);
  useEffect(() => {
    if (uninstallOpen) {
      dialog.current?.showModal();
      cancel.current?.focus();
    } else {
      dialog.current?.close();
    }
  }, [uninstallOpen]);
  useEffect(() => {
    if (details) back.current?.focus();
  }, [details]);

  const size = row?.available ? `${(row.available.size / 1024 / 1024).toFixed(1)} MB` : "";
  const updating = Boolean(row?.installed && row.available && row.version !== row.available.version);
  const status = row?.installed ? row.enabled ? "已开启" : "已关闭" : "未安装";
  const install = row && (!row.installed || updating || !row.compatible) && <button type="button" className="btn primary small" disabled={busy} onClick={() => void run(api.installExtension)}>
    <Download aria-hidden="true" />{row.installed ? updating ? "更新" : "重新安装" : "下载并安装"}
  </button>;
  const openUninstall = () => {
    setClearData(false);
    setError("");
    setUninstallOpen(true);
  };
  const failure = error || (query.error ? spaceError(query.error) : "") || row?.error;

  return <section className="settings-section-enter extensions-page">
    <div className="settings-title"><h2>扩展管理</h2><p>按需安装功能，随时开启、关闭或卸载。</p></div>
    {!row && !query.error && <p role="status">正在读取扩展状态…</p>}
    {row && <>
      <div className="extensions-toolbar">
        {details ? <button ref={back} type="button" className="extension-back" onClick={() => {
          setDetails(false);
          requestAnimationFrame(() => detailsButton.current?.focus());
        }}><ArrowLeft aria-hidden="true" />所有扩展</button> : <h3>{row.installed ? "已安装的扩展" : "获取扩展"}</h3>}
        <button type="button" className="btn small" disabled={busy} onClick={() => void run(api.extensionCatalog)}><RefreshCw aria-hidden="true" />检查更新</button>
      </div>
      <article className="extension-card extension-manager-card" aria-label="AI 研究助手">
        <div className="extension-card-heading">
          <div className="extension-icon"><Plug aria-hidden="true" /></div>
          <div className="extension-summary">
            <div className="extension-name"><h3>AI 研究助手</h3><span className="extension-version">{row.version || row.available?.version}</span></div>
            <p>围绕空间资料聊天、整理与引用，回答可写入笔记。</p>
          </div>
          {row.installed && <button type="button" className="switch extension-toggle" role="switch" aria-checked={row.enabled} aria-label="开启 AI 研究助手" disabled={busy || !row.compatible} onClick={() => void run(() => api.enableExtension(!row.enabled))} />}
        </div>
        <div className="extension-card-content">
          <p className="extension-meta"><span className={`extension-state${row.enabled ? " is-enabled" : ""}`}>{status}</span><span>官方扩展 · SiYe-AI</span></p>
          {details && <div className="extension-details">
            <dl><div><dt>版本</dt><dd>{row.version || row.available?.version || "暂未获取"}</dd></div><div><dt>下载大小</dt><dd>{size || "暂未获取"}</dd></div><div><dt>来源</dt><dd>四野官方 GitHub Release</dd></div></dl>
            <p className="extension-hint">使用前需要配置模型服务。关闭或卸载后，空间资料和已写入的笔记继续保留。</p>
            {row.installed && !row.compatible && <p className="extension-error">当前扩展不兼容或安装不完整，请重新安装后开启。</p>}
          </div>}
          <div className="extension-actions">
            {!details && <button ref={detailsButton} type="button" className="btn small" onClick={() => setDetails(true)}>详细信息</button>}
            {row.installed && <button type="button" className="btn small" disabled={busy} onClick={openUninstall}>卸载</button>}
            {install}
          </div>
          {busy && <div className="extension-progress" role="status"><LoaderCircle className="animate-spin" aria-hidden="true" /><span>{row.phase === "downloading" ? `正在下载${row.total ? ` ${Math.min(100, Math.floor(row.downloaded / row.total * 100))}%` : ""}` : row.phase === "installing" ? "正在安装…" : row.phase === "stopping" ? "正在停止任务…" : row.phase === "uninstalling" ? "正在卸载…" : "正在处理…"}</span>{["downloading", "installing"].includes(row.phase) && <button type="button" className="text-link" disabled={pending} onClick={() => void run(api.cancelExtensionInstall)}>取消</button>}</div>}
        </div>
      </article>
      {details && row.enabled && <div className="extension-options"><h3>扩展选项</h3><PluginSettings /></div>}
      {details && !row.enabled && <p className="extension-hint extension-options-hint">{row.installed ? "开启扩展后，可以在这里配置模型服务。" : "安装并开启扩展后，可以在这里配置模型服务。"}</p>}
    </>}
    {failure && !uninstallOpen && <p role="alert" className="extension-error">{failure}</p>}
    <dialog ref={dialog} className="confirm-card extension-uninstall-dialog" aria-labelledby="extension-uninstall-title" aria-describedby="extension-uninstall-description" onKeyDown={(event) => {
      if (event.key !== "Tab") return;
      const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)')];
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }} onCancel={(event) => {
      event.preventDefault();
      if (!busy) setUninstallOpen(false);
    }} onClose={() => setUninstallOpen(false)}>
      <h2 id="extension-uninstall-title" className="confirm-title">卸载 AI 研究助手？</h2>
      <p id="extension-uninstall-description" className="confirm-text">卸载后将移除扩展程序。空间资料和已写入的笔记继续保留。</p>
      <label className="confirm-remember"><input type="checkbox" checked={clearData} disabled={busy} onChange={(event) => setClearData(event.target.checked)} /><span>同时清除 AI 配置和聊天记录</span></label>
      <p className="confirm-hint">默认保留配置和聊天记录，重新安装后可以继续使用。</p>
      {failure && uninstallOpen && <p role="alert" className="extension-error">{failure}</p>}
      <div className="confirm-actions"><button ref={cancel} type="button" className="btn" disabled={busy} onClick={() => setUninstallOpen(false)}>取消</button><button type="button" className="btn danger-solid" disabled={busy} onClick={async () => {
        if (await run(() => api.uninstallExtension(clearData))) setUninstallOpen(false);
      }}>{pending ? "正在卸载…" : "卸载"}</button></div>
    </dialog>
  </section>;
}
