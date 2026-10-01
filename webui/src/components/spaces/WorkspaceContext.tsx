import { createContext, useContext, type ReactNode } from "react";
export const WorkspaceContext = createContext<{ setDetailOpen: (value: boolean) => void; hasNote: boolean; noteSlot: ReactNode } | null>(null);
export const useResearchWorkspace = () => useContext(WorkspaceContext);
