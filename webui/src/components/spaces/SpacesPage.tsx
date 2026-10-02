import { useState } from "react";
import { Archive, ArrowLeft, Layers, Pencil, Play, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { useBookmarks } from "@/hooks/useBookmarks";
import { useSpaceDetail, useSpaces } from "@/hooks/useSpaces";
import { removeSpaceItem, type ResearchSpace } from "@/lib/spacesApi";
import { spaceError } from "@/lib/spaceNotes";
import { ResultTabs } from "@/components/search/ResultTabs";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { PLATFORM_SLUGS } from "@/lib/platformMeta";
import { SpaceWorkspace } from "./SpaceWorkspace";
import { SpaceInfoDialog } from "./SpaceInfoDialog";

export function SpacesPage({ spaceId, onReturnSearch }: { spaceId: number | null; onReturnSearch: () => void }) {
  const { t } = useTranslation();
  const spaces = useSpaces();
  const detail = useSpaceDetail(spaceId);
  const library = useBookmarks();
  const [tab, setTab] = useState(false);
  const [editing, setEditing] = useState<ResearchSpace | "new" | null>(null);
  const [deleting, setDeleting] = useState<ResearchSpace | null>(null);
  const [removing, setRemoving] = useState(false);
  const space = detail.data;
  return <div className="preview-container spaces-page">
    {spaceId === null ? <>
      <div className="page-heading"><div><p className="eyebrow">{t("spaces.eyebrow")}</p><h1>{t("spaces.title")}</h1><p className="description">{t("spaces.intro")}</p></div><button type="button" className="btn primary" disabled={spaces.busy || spaces.loading || !!spaces.error} onClick={() => setEditing("new")}>{t("spaces.create")}</button></div>
      <div className="space-list-tabs" role="tablist" aria-label={t("spaces.title")}><button type="button" role="tab" aria-selected={!tab} onClick={() => setTab(false)}>{t("spaces.ongoing")} <span>{spaces.spaces.filter((item) => !item.archived).length}</span></button><button type="button" role="tab" aria-selected={tab} onClick={() => setTab(true)}>{t("spaces.archived")} <span>{spaces.spaces.filter((item) => item.archived).length}</span></button></div>
      {spaces.loading && <p role="status">{t("spaces.loading")}</p>}
      {spaces.error && <p role="alert">{spaces.error}<button type="button" className="text-link" onClick={() => void spaces.refresh()}>{t("spaces.retry")}</button></p>}
      <div className="space-library-grid">{spaces.spaces.filter((item) => item.archived === tab).map((item) => <article className="space-library-card" key={item.id}>
        <a href={`#/spaces/${item.id}`} className="space-library-open"><div className="space-library-symbol"><Layers aria-hidden="true" />{spaces.activeId === item.id && <span>{t("spaces.collecting")}</span>}</div><h2>{item.name}</h2><p>{item.description || t("spaces.noDescription")}</p><div className="space-library-meta"><span>{t("spaces.materialCount", { count: item.item_count })}</span><time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleDateString()}</time></div></a>
        <div className="space-library-actions"><button type="button" className="text-link" disabled={spaces.busy} onClick={() => void (item.archived ? spaces.archive(item.id, false) : spaces.activate(item.id))}><Play aria-hidden="true" />{t(item.archived ? "spaces.resume" : spaces.activeId === item.id ? "spaces.collecting" : "spaces.activate")}</button><button type="button" className="text-link" aria-label={`${t("spaces.discard")}：${item.name}`} disabled={spaces.busy} onClick={() => setDeleting(item)}><Trash2 aria-hidden="true" />{t("spaces.discard")}</button></div>
      </article>)}</div>
      {!spaces.loading && !spaces.error && !spaces.spaces.some((item) => item.archived === tab) && <div className="space-empty"><Layers aria-hidden="true" /><h2>{t(tab ? "spaces.noArchived" : "spaces.noSpaces")}</h2><p>{t(tab ? "spaces.archiveHint" : "spaces.emptyHint")}</p></div>}
    </> : <>
      <div className="space-return-actions"><button type="button" className="btn primary small" onClick={onReturnSearch}><ArrowLeft aria-hidden="true" />{t("spaces.returnSearch")}</button><a href="#/spaces" className="text-link"><Layers aria-hidden="true" />{t("spaces.back")}</a></div>
      {detail.isPending && <p role="status">{t("spaces.loading")}</p>}
      {detail.isError && <p role="alert">{spaceError(detail.error)}<button type="button" className="text-link" onClick={() => void detail.refetch()}>{t("spaces.retry")}</button></p>}
      {space && <>
        <div className="page-heading"><div><p className="eyebrow">{t(space.archived ? "spaces.archived" : spaces.activeId === space.id ? "spaces.collecting" : "spaces.ongoing")}</p><h1>{space.name}</h1>{space.description && <p className="description">{space.description}</p>}</div><div className="space-page-actions">
          {!space.archived && <button type="button" className="btn small" disabled={spaces.busy} onClick={() => setEditing(space)}><Pencil aria-hidden="true" />{t("spaces.edit")}</button>}
          {(space.archived || spaces.activeId !== space.id) && <button type="button" className="btn primary small" disabled={spaces.busy} onClick={() => void (space.archived ? spaces.archive(space.id, false) : spaces.activate(space.id))}><Play aria-hidden="true" />{t(space.archived ? "spaces.resume" : "spaces.activate")}</button>}
          {!space.archived && <button type="button" className="btn small" disabled={spaces.busy} onClick={() => void spaces.archive(space.id, true)}><Archive aria-hidden="true" />{t("spaces.archive")}</button>}
          <button type="button" className="btn danger small" disabled={spaces.busy} onClick={() => setDeleting(space)}><Trash2 aria-hidden="true" />{t("spaces.discard")}</button>
        </div></div>
        <SpaceWorkspace spaceId={space.id} panelsHidden={!!editing || !!deleting}><ResultTabs viewScope="spaces" results={space.items.map((item) => item.result)} overall="completed" platforms={[...PLATFORM_SLUGS]} library={library} disableSort pageSize={100} showExportTools={false} emptyMessage={t("spaces.noMaterials")}
          renderExtraActions={!space.archived ? (result) => <button type="button" className="text-cyber-text-muted" aria-label={`${t("spaces.removeMaterial")}：${result.title}`} title={t("spaces.removeMaterial")} disabled={removing} onClick={async () => {
            if (removing) return;
            setRemoving(true);
            try { await removeSpaceItem(space.id, result); await spaces.refresh(); }
            catch (error) { toast.error(spaceError(error)); }
            finally { setRemoving(false); }
          }}><Trash2 aria-hidden="true" /></button> : undefined} />
        </SpaceWorkspace>
      </>}
    </>}
    {editing && <SpaceInfoDialog space={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    <ConfirmDialog open={Boolean(deleting)} title={t("spaces.discardTitle")} description={t("spaces.discardDescription", { name: deleting?.name })} confirmLabel={t("spaces.discard")} danger busy={spaces.busy} onCancel={() => { if (!spaces.busy) setDeleting(null); }} onConfirm={async () => {
      if (deleting && await spaces.remove(deleting.id)) { setDeleting(null); if (spaceId === deleting.id) window.location.hash = "/spaces"; }
    }} />
  </div>;
}
