import { useEffect, useState } from "react";
import type { useFavorites } from "@/hooks/useFavorites";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { PLATFORM_LABELS, type PlatformSlug, type RemoteArchiveItem } from "@/types/search";

export function RemoteArchiveControls({ remote }: { remote: ReturnType<typeof useFavorites> }) {
  const [selected, setSelected] = useState<number[]>([]);
  const [removing, setRemoving] = useState<RemoteArchiveItem[]>([]);
  const [message, setMessage] = useState("");
  const items = remote.archive?.items ?? [];
  useEffect(() => { setSelected([]); }, [remote.page, remote.platform, remote.state, remote.account]);
  const pending = items.filter(item => item.state === "pending");
  const apply = async (rows: RemoteArchiveItem[], action: "keep" | "remove") => {
    try {
      const result = await remote.resolve.mutateAsync(rows.flatMap(item => item.missing_batch
        ? [{ id: item.id, missing_batch: item.missing_batch, action }] : []));
      setMessage(`已处理 ${result.applied} 条${result.stale ? `；${result.stale} 条状态已变化或已处理，未重复操作` : ""}`);
      setSelected([]);
      setRemoving([]);
    } catch { /* The hook exposes the error beside the archive. */ }
  };
  return <section aria-label="跨平台收藏归档">
    <p className="collection-count">B站不设总条数上限；目前增量排序尚未验证，更新会读取完整列表，不逐条补详情。其他平台仍最多 100 条。</p>
    {remote.data?.accounts?.map(account => <p className="collection-count" key={account.account}>
      账号 {account.account} · {account.last_full ? `最近完整核对：${new Date(account.last_full).toLocaleString("zh-CN")}` : "尚未完整核对"}
      {account.full_due && " · 建议执行完整核对"}
    </p>)}
    <div className="library-batch-bar">
      <button className="btn" disabled={remote.busy} onClick={() => void remote.sync(["bilibili"], "full")}>完整核对 B站</button>
      <button className="btn" onClick={() => remote.setState(remote.state === "pending" ? "" : "pending")}>
        {remote.state === "pending" ? "返回全部归档" : `待确认 ${remote.data?.pending_count ?? 0} 条`}
      </button>
      <select className="field select" aria-label="归档平台" value={remote.platform} onChange={event => remote.setPlatform(event.target.value as PlatformSlug | "")}>
        <option value="">全部平台</option>
        {Object.entries(PLATFORM_LABELS).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
      </select>
      <select className="field select" aria-label="归档状态" value={remote.state} onChange={event => remote.setState(event.target.value)}>
        <option value="">全部状态</option><option value="present">平台中已找到</option><option value="pending">待确认</option>
        <option value="archived">保留的缺失归档</option><option value="legacy">历史归档／账号未确认</option>
      </select>
      <select className="field select" aria-label="归档账号" value={remote.account} onChange={event => remote.setAccount(event.target.value)}>
        <option value="">全部账号</option><option value="default">历史归档／账号未确认</option>
        {remote.data?.accounts?.map(account => <option key={account.account} value={account.account}>{account.account}</option>)}
      </select>
    </div>
    {pending.length > 0 && <div className="status-line">
      <div>
        <p>以下内容在平台收藏中未找到，可能已取消、失效或不可见。请选择是否保留；暂不处理会继续保留。</p>
        <label><input type="checkbox" checked={pending.every(item => selected.includes(item.id))}
          onChange={event => setSelected(event.target.checked ? pending.map(item => item.id) : [])} />选择本页待确认条目</label>
        <ul>{pending.map(item => <li key={item.id}>
          <label><input type="checkbox" checked={selected.includes(item.id)} onChange={event => setSelected(before => event.target.checked ? [...before, item.id] : before.filter(id => id !== item.id))} />{item.result.title || item.result.content_id}（{item.account}）</label>
          <button className="text-link" disabled={remote.resolve.isPending} onClick={() => void apply([item], "keep")}>保留归档</button>
          <button className="text-link" disabled={remote.resolve.isPending} onClick={() => setRemoving([item])}>从跨平台收藏移除</button>
        </li>)}</ul>
        <button className="btn" disabled={!selected.length || remote.resolve.isPending} onClick={() => void apply(pending.filter(item => selected.includes(item.id)), "keep")}>保留选中条目</button>
        <button className="btn" disabled={!selected.length || remote.resolve.isPending} onClick={() => setRemoving(pending.filter(item => selected.includes(item.id)))}>移除选中条目</button>
      </div>
    </div>}
    {items.some(item => item.state === "archived" || item.state === "legacy") && <ul className="collection-count">
      {items.filter(item => item.state === "archived" || item.state === "legacy").map(item => <li key={item.id}>
        {item.result.title || item.result.content_id}：{item.state === "archived" ? "平台收藏中未找到 · 已保留归档" : "历史归档／账号未确认"}
      </li>)}
    </ul>}
    {message && <p role="status">{message}</p>}
    <div className="library-batch-bar">
      <span>共 {remote.archive?.total ?? 0} 条 · 第 {remote.page + 1} 页 · 每页 50 条</span>
      <button className="btn" disabled={remote.page === 0 || remote.loadingArchive} onClick={() => remote.setPage(remote.page - 1)}>上一页</button>
      <button className="btn" disabled={remote.loadingArchive || (remote.page + 1) * 50 >= (remote.archive?.total ?? 0)} onClick={() => remote.setPage(remote.page + 1)}>下一页</button>
    </div>
    <p className="collection-count">下方结果内的筛选、排序和导出仅作用于当前页。</p>
    <ConfirmDialog open={removing.length > 0} title={`从跨平台收藏移除 ${removing.length} 条？`}
      description="只移除同步归档及远端归属，不会删除本地收藏副本、备注或本地收藏夹。以后在平台再次发现时仍可重新导入。"
      confirmLabel="从跨平台收藏移除" danger busy={remote.resolve.isPending}
      onConfirm={() => void apply(removing, "remove")} onCancel={() => setRemoving([])} />
  </section>;
}
