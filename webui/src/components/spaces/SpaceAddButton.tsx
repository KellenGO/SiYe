import { useMemo, useRef, useState } from "react";
import { Check, Layers, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { useSpaceDetail, useSpaces } from "@/hooks/useSpaces";
import { addSpaceItems, removeSpaceItem, spaceSources } from "@/lib/spacesApi";
import { resultKey } from "@/lib/resultTools";
import { spaceError } from "@/lib/spaceNotes";
import type { UnifiedSearchResult } from "@/types/search";

export function SpaceAddButton({ result, showLabel = false }: { result: UnifiedSearchResult; showLabel?: boolean }) {
  const { t } = useTranslation();
  const spaces = useSpaces();
  const detail = useSpaceDetail(spaces.activeId);
  const [pending, setPending] = useState(false);
  const pendingRef = useRef(false);
  // 搜索响应里可能混入不能持久化的卡片（例如知乎问题对象）。校验失败时
  // 只是不给这个操作入口，不能让一次渲染异常把整个页面卸载成白屏。
  const sources = useMemo(() => {
    try { return spaceSources(result); } catch { return null; }
  }, [result]);
  if (!spaces.activeId || !detail.data || detail.data.archived || !sources?.length) return null;
  const id = spaces.activeId;
  const present = new Set(detail.data.items.map((item) => item.key));
  const added = sources.every((source) => present.has(resultKey(source)));
  const label = t(added ? sources.length > 1 ? "spaces.removeVersions" : "spaces.removeMaterial" : sources.length > 1 ? "spaces.addVersions" : "spaces.add");
  return <button type="button" className={`space-add ${added ? "is-added" : ""}`} title={`${label} · ${detail.data.name}`} aria-label={`${label}：${result.title}`} aria-pressed={added} disabled={pending || spaces.busy} onClick={async () => {
    if (pendingRef.current || spaces.busy) return;
    pendingRef.current = true;
    setPending(true);
    try { await (added ? removeSpaceItem(id, result) : addSpaceItems(id, result)); await spaces.refresh(); }
    catch (error) { toast.error(spaceError(error)); }
    finally { pendingRef.current = false; setPending(false); }
  }}>{pending ? <Loader2 className="animate-spin" aria-hidden="true" /> : added ? <Check aria-hidden="true" /> : <Layers aria-hidden="true" />}{showLabel && <span>{label}</span>}</button>;
}
