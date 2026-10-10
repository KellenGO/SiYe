import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Download, LoaderCircle, Plug, Trash2 } from "lucide-react";
import { useExtensions } from "@/hooks/useExtensions";
import * as api from "@/lib/extensionsApi";
import { spaceError } from "@/lib/spaceNotes";
import { PluginSettings } from "@/components/extensions/PluginUI";

export function ExtensionsPage() {
  const query = useExtensions();
  const client = useQueryClient();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [clearData, setClearData] = useState(false);
  const row = query.data;
  const busy = pending || Boolean(row && ["downloading", "installing", "stopping", "uninstalling"].includes(row.phase));
  const run = async (action: () => Promise<api.ExtensionStatus>) => {
    if (pending) return;
    setPending(true); setError("");
    try {
      const next = await action();
      client.setQueryData(["extensions-ai"], next);
      client.removeQueries({ predicate: (item) => String(item.queryKey[0]).startsWith("research-") });
    } catch (cause) { setError(spaceError(cause)); }
    finally { setPending(false); }
  };
  useEffect(() => { void api.extensionCatalog().then((next) => client.setQueryData(["extensions-ai"], next)).catch(() => {}); }, [client]);
  const size = row?.available ? `${(row.available.size / 1024 / 1024).toFixed(1)} MB` : "";
  const updating = Boolean(row?.installed && row.available && row.version !== row.available.version);
  return <section className="settings-section-enter extensions-page">
    <div className="settings-title"><h2>扩展管理</h2><p>按需安装功能，随时开启、关闭或卸载。</p></div>
    {!row && !query.error && <p role="status">正在读取扩展状态…</p>}
    {row && <article className="extension-card">
      <div className="extension-card-heading"><Plug aria-hidden="true" /><div><h3>AI 研究助手</h3><p>围绕空间资料聊天、整理与引用，回答可写入笔记。</p></div><span className="extension-state">{row.installed ? row.enabled ? "已开启" : "已关闭" : "未安装"}</span></div>
      <p className="extension-meta">{row.version ? `版本 ${row.version}` : "官方扩展"}{size && ` · ${size}`} · 使用时需要配置模型服务</p>
      <div className="extension-actions">
        {(!row.installed || updating || !row.compatible) && <button type="button" className="btn primary small" disabled={busy} onClick={() => void run(api.installExtension)}><Download aria-hidden="true" />{row.installed ? updating ? "更新" : "重新安装" : "下载并安装"}</button>}
        {row.installed && <><label className="extension-switch"><input type="checkbox" role="switch" aria-label="开启 AI 研究助手" checked={row.enabled} disabled={busy || !row.compatible} onChange={(event) => void run(() => api.enableExtension(event.target.checked))} /><span>开启 AI 研究助手</span></label><button type="button" className="btn small" disabled={busy} onClick={() => void run(() => api.uninstallExtension(clearData))}><Trash2 aria-hidden="true" />卸载</button></>}
        <button type="button" className="text-link" disabled={busy} onClick={() => void run(api.extensionCatalog)}>检查更新</button>
      </div>
      {row.installed && <label className="extension-clear-data"><input type="checkbox" checked={clearData} disabled={busy} onChange={(event) => setClearData(event.target.checked)} />卸载时同时清除 AI 配置和聊天记录</label>}
      {busy && <div className="extension-progress" role="status"><LoaderCircle className="animate-spin" aria-hidden="true" /><span>{row.phase === "downloading" ? "正在下载" : row.phase === "installing" ? "正在安装" : "正在处理"}{row.phase === "downloading" && row.total ? ` · ${Math.min(100, Math.floor(row.downloaded / row.total * 100))}%` : ""}</span>{["downloading", "installing"].includes(row.phase) && <button type="button" className="text-link" onClick={() => void run(api.cancelExtensionInstall)}>取消</button>}</div>}
      {!row.enabled && <p className="extension-hint">关闭或卸载后，空间资料和已写入的笔记继续保留。</p>}
      {row.error && <p role="alert" className="extension-error">{row.error}</p>}
    </article>}
    {(error || query.error) && <p role="alert" className="extension-error">{error || spaceError(query.error)}</p>}
    {row?.enabled && <PluginSettings />}
  </section>;
}
