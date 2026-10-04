export function transcriptDuration(seconds?: number): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "";
  const whole = Math.floor(seconds);
  const minutes = Math.floor(whole / 60);
  return `${minutes.toString().padStart(2, "0")}:${(whole % 60).toString().padStart(2, "0")}`;
}
