import { lazy, Suspense, useCallback, useState, type ReactNode } from "react";
import { Layers, NotebookPen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSpaceDetail, useSpaces } from "@/hooks/useSpaces";
import { SpaceEdgePanel } from "./SpaceEdgePanel";
import { SpaceInfoDialog } from "./SpaceInfoDialog";
import { WorkspaceContext } from "./WorkspaceContext";
const SpaceNoteController = lazy(() => import("./SpaceNoteController"));

export function SpaceWorkspace({ spaceId, selector = false, active = true, panelsHidden = false, children }: { spaceId: number | null; selector?: boolean; active?: boolean; panelsHidden?: boolean; children: ReactNode }) {
  const { t } = useTranslation();
  const spaces = useSpaces();
  const detail = useSpaceDetail(spaceId);
  const [open, setOpen] = useState(false);
  const [spaceOpen, setSpaceOpen] = useState(false);
  const [spacePinned, setSpacePinned] = useState(false);
  const [notePinned, setNotePinned] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [inlineHost, setInlineHost] = useState<HTMLDivElement | null>(null);
  const [drawerHost, setDrawerHost] = useState<HTMLDivElement | null>(null);
  const [notesVisited, setNotesVisited] = useState(false);
  const changeNotes = useCallback((value: boolean) => { if (value) setNotesVisited(true); else setNotePinned(false); setOpen(value); }, []);
  const closeNotes = useCallback(() => changeNotes(false), [changeNotes]);
  const hasNote = Boolean(active && !panelsHidden && !creating && spaceId && detail.data && open);
  const noteSlot = hasNote ? <div className="space-note-slot" ref={setDrawerHost} /> : null;
  const hidePanels = !active || detailOpen || creating || panelsHidden;
  return <WorkspaceContext.Provider value={{ setDetailOpen, hasNote, noteSlot }}>
    {active && spaceId && (open || notesVisited) && <Suspense fallback={null}><SpaceNoteController spaceId={spaceId} open={open && !creating && !panelsHidden} onClose={closeNotes} target={detailOpen ? drawerHost : inlineHost} /></Suspense>}
    {selector && <SpaceEdgePanel side="left" label={t("spaces.current")} icon={<Layers aria-hidden="true" />} open={spaceOpen} pinned={spacePinned} hidden={hidePanels} closeLabel={t("spaces.closeSpacePanel")} onOpenChange={setSpaceOpen} onPinChange={setSpacePinned}>
        <div className="space-search-bar">
          <label><span>{t("spaces.current")}</span><select aria-label={t("spaces.current")} value={spaces.activeId ?? ""} disabled={spaces.loading || spaces.busy} onChange={(event) => { void spaces.activate(event.target.value ? Number(event.target.value) : null); }}>
            <option value="">{t("spaces.notCollecting")}</option>{spaces.spaces.filter((space) => !space.archived).map((space) => <option key={space.id} value={space.id}>{space.name}</option>)}
          </select></label><button type="button" className="btn small" disabled={spaces.busy || spaces.loading} onClick={() => setCreating(true)}>{t("spaces.create")}</button>
          {spaces.activeId && <a className="btn primary small" href={`#/spaces/${spaces.activeId}`}>{t("spaces.viewMaterials")}</a>}
          {!spaces.activeId && <span className="space-search-hint">{t("spaces.startHint")}</span>}
          {spaces.error && <span role="alert">{spaces.error}<button className="text-link" type="button" onClick={() => void spaces.refresh()}>{t("spaces.retry")}</button></span>}
        </div>
      </SpaceEdgePanel>}
      {spaceId && <SpaceEdgePanel side="right" label={t("spaces.note")} triggerLabel={t("spaces.openNote")} icon={<NotebookPen aria-hidden="true" />} open={open} pinned={notePinned} hidden={hidePanels} disabled={!detail.data} closeLabel={t("spaces.closeNote")} onOpenChange={changeNotes} onPinChange={setNotePinned}>
        <div className="space-note-slot" ref={setInlineHost} />
      </SpaceEdgePanel>}
      {active && detail.isError && <p className="status-line" role="alert">{t("spaces.loadFailed")}<button type="button" className="text-link" onClick={() => void detail.refetch()}>{t("spaces.retry")}</button></p>}
    <div className="space-workspace"><div className="space-workspace-main">{children}</div></div>
    {active && creating && <SpaceInfoDialog onClose={() => setCreating(false)} />}
  </WorkspaceContext.Provider>;
}
