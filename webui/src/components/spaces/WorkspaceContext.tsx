import { createContext, useContext, type ReactNode } from "react";
export type WorkspaceSplitLayout = {
  active: boolean; noteWidth: number; historyOpen: boolean;
  enter: () => void; restore: () => void; resize: (noteWidth: number) => void; setHistoryOpen: (open: boolean) => void;
};
export const WorkspaceContext = createContext<{ setDetailOpen: (value: boolean) => void; hasNote: boolean; noteSlot: ReactNode } | null>(null);
export const useResearchWorkspace = () => useContext(WorkspaceContext);
