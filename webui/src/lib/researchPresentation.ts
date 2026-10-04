export function transcriptDuration(seconds?: number): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "";
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  return `${minutes.toString().padStart(2, "0")}:${(whole % 60).toString().padStart(2, "0")}`;
}

export function sectionReading(section: { chunks: number; read_chunks: number; reading_complete?: boolean; content_limited?: boolean }, comments: boolean): string {
  if (!section.chunks) return "research.sectionUnavailable";
  if (comments && !section.content_limited && (section.reading_complete ?? section.read_chunks === section.chunks)) return "research.sampleRead";
  return "research.sectionRead";
}
