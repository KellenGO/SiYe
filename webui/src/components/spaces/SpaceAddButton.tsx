import { useState } from "react";
import { Check, Layers, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { useSpaceDetail, useSpaces } from "@/hooks/useSpaces";
import { addSpaceItems, spaceSources } from "@/lib/spacesApi";
import { resultKey } from "@/lib/resultTools";
import { spaceError } from "@/lib/spaceNotes";
import type { UnifiedSearchResult } from "@/types/search";

export function SpaceAddButton({ result }: { result: UnifiedSearchResult }) {
  const { t } = useTranslation();
  const spaces = useSpaces();
  const detail = useSpaceDetail(spaces.activeId);
  const [pending, setPending] = useState(false);
  if (!spaces.activeId || !detail.data || detail.data.archived) return null;
  const id = spaces.activeId;
  const sources = spaceSources(result);
  const present = new Set(detail.data.items.map((item) => item.key));
  const added = sources.every((source) => present.has(resultKey(source)));
  const label = t(added ? "spaces.added" : sources.length > 1 ? "spaces.addVersions" : "spaces.add");
  return <button type="button" className={`space-add ${added ? "is-added" : ""}`} title={`${label} · ${detail.data.name}`} aria-label={`${label}：${result.title}`} disabled={added || pending || spaces.busy} onClick={async () => {
    if (pending) return;
    setPending(true);
    try { await addSpaceItems(id, result); await spaces.refresh(); }
    catch (error) { toast.error(spaceError(error)); }
    finally { setPending(false); }
  }}>{pending ? <Loader2 className="animate-spin" aria-hidden="true" /> : added ? <Check aria-hidden="true" /> : <Layers aria-hidden="true" />}<span>{label}</span></button>;
}
