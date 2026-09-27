/** 已保存的小红书带时间戳地址可能失效；仅对已识别的 CDN 路径尝试原图。 */
export function xhsCoverFallback(cover: string | null, platform: string): string | null {
  if (platform !== "xhs" || !cover) return null;
  try {
    const url = new URL(cover.startsWith("//") ? `https:${cover}` : cover);
    if (!["http:", "https:"].includes(url.protocol) || !/^sns-webpic(?:-[a-z0-9]+)?\.xhscdn\.com$/.test(url.hostname)) return null;
    const match = url.pathname.match(/^\/\d{12,14}\/[a-f0-9]{32}\/((?:spectrum\/)?[a-zA-Z0-9_-]+)(?:![^/]*)?$/);
    return match ? `https://sns-img-qc.xhscdn.com/${match[1]}` : null;
  } catch { return null; }
}
