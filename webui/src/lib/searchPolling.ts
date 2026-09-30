import type { SearchJobResponse } from "../types/search.js";

/** Updated backends hold the request until progress; old backends still poll. */
export function searchPollInterval(
  data?: Pick<SearchJobResponse, "overall" | "completed_at" | "hydration_status" | "revision">,
): number | false {
  if (!data) return 250;
  const terminal = ["completed", "partial", "failed", "cancelled"].includes(data.overall);
  if (terminal && data.completed_at && data.hydration_status !== "running") return false;
  if (Number.isInteger(data.revision) && data.revision! >= 0) return 10;
  if (!terminal || !data.completed_at) return 250;
  return data.hydration_status === "running" ? 800 : false;
}

export function searchWaitParams(revision?: number) {
  return Number.isInteger(revision) && revision! >= 0
    ? { after_revision: revision, wait_seconds: 15 }
    : undefined;
}
