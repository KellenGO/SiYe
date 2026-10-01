import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as api from "@/lib/spacesApi";
import { NoteSession, spaceError } from "@/lib/spaceNotes";

function useSpacesState() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["spaces"], queryFn: api.fetchSpaces, retry: false });
  const sessions = useRef(new Map<number, NoteSession>());
  const [, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const mutationPending = useRef(false);
  const changed = useCallback(() => setGeneration((value) => value + 1), []);
  const refresh = useCallback(async () => {
    await Promise.all([client.invalidateQueries({ queryKey: ["spaces"] }), client.invalidateQueries({ queryKey: ["space-detail"] })]);
  }, [client]);
  const session = useCallback((detail: api.SpaceDetail) => {
    let current = sessions.current.get(detail.id);
    if (!current) {
      current = new NoteSession(detail.note_document, detail.note_revision, async (document, revision) => {
        const result = await api.saveSpaceNote(detail.id, document, revision);
        void client.invalidateQueries({ queryKey: ["spaces"] });
        return result;
      }, changed);
      sessions.current.set(detail.id, current);
    }
    return current;
  }, [changed, client]);
  const flushAll = useCallback(async () => {
    for (const current of sessions.current.values()) {
      if (!await current.flush()) {
        toast.error(current.error || "笔记尚未保存，请先重试保存");
        return false;
      }
    }
    return true;
  }, []);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if ([...sessions.current.values()].some((current) => current.dirty)) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);

  const run = async (operation: () => Promise<unknown>, discardId?: number): Promise<boolean> => {
    if (mutationPending.current) return false;
    mutationPending.current = true;
    setBusy(true);
    try {
      if (discardId === undefined && !await flushAll()) return false;
      await operation();
      if (discardId !== undefined) { sessions.current.get(discardId)?.dispose(); sessions.current.delete(discardId); }
      await refresh();
      return true;
    } catch (error) {
      toast.error(spaceError(error));
      return false;
    } finally { mutationPending.current = false; setBusy(false); }
  };
  return { spaces: query.data?.spaces ?? [], activeId: query.data?.active_space_id ?? null,
    loading: query.isPending, error: query.error ? spaceError(query.error) : "", busy, session, flushAll, refresh,
    create: (name: string, description: string) => run(() => api.createSpace(name, description)),
    activate: (id: number | null) => run(() => api.activateSpace(id)),
    update: (id: number, name: string, description: string) => run(() => api.updateSpace(id, name, description)),
    archive: (id: number, archived: boolean) => run(() => api.archiveSpace(id, archived)),
    remove: (id: number) => run(async () => {
      const current = sessions.current.get(id);
      current?.cancelScheduledSave();
      if (current?.saving) await current.flush();
      await api.deleteSpace(id);
    }, id),
  };
}

const SpacesContext = createContext<ReturnType<typeof useSpacesState> | null>(null);
export function SpacesProvider({ children }: { children: ReactNode }) {
  const state = useSpacesState();
  return <SpacesContext.Provider value={state}>{children}</SpacesContext.Provider>;
}
export function useSpaces() {
  const state = useContext(SpacesContext);
  if (!state) throw new Error("SpacesProvider is required");
  return state;
}
export function useSpaceDetail(id: number | null) {
  return useQuery({ queryKey: ["space-detail", id], queryFn: () => api.fetchSpace(id!), enabled: id !== null, retry: false });
}
