import type { SearchJobResponse } from "../types/search.js";

/** Poll only our local backend faster while results are arriving. */
export function searchPollInterval(
  data?: Pick<SearchJobResponse, "overall" | "completed_at" | "hydration_status">,
): number | false {
  if (!data) return 250;
  const terminal = ["completed", "partial", "failed", "cancelled"].includes(data.overall);
  if (!terminal || !data.completed_at) return 250;
  return data.hydration_status === "running" ? 800 : false;
}
