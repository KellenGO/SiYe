import type { UnifiedSearchResult } from "@/types/search";

export interface ReadingBlock {
  type: "paragraph" | "heading" | "quote" | "code" | "list-item" | "image" | "unsupported";
  text: string;
  url?: string;
  level?: number;
}

export interface ReadingDetail {
  content_id: string;
  content_type: string;
  blocks: ReadingBlock[];
  limited: boolean;
  notices: string[];
}

export function supportsReading(source: Pick<UnifiedSearchResult, "platform" | "content_type" | "content_id" | "url">): boolean {
  if (source.platform !== "zhihu" || !/^[0-9]{1,30}$/.test(source.content_id)) return false;
  try {
    const url = new URL(source.url);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    if (source.content_type === "article") return url.hostname === "zhuanlan.zhihu.com" && url.pathname.replace(/\/$/, "") === `/p/${source.content_id}`;
    const match = url.pathname.match(/^\/(?:question\/[0-9]+\/)?answer\/([0-9]+)\/?$/);
    return source.content_type === "answer" && ["zhihu.com", "www.zhihu.com"].includes(url.hostname) && match?.[1] === source.content_id;
  } catch { return false; }
}

export function readingImageUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value.startsWith("//") ? `https:${value}` : value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port || !(url.hostname === "zhimg.com" || url.hostname.endsWith(".zhimg.com"))) return null;
    url.protocol = "https:";
    return url.href;
  } catch { return null; }
}

export function decodeReadingDetail(raw: unknown, source: Pick<UnifiedSearchResult, "content_id" | "content_type">): ReadingDetail {
  const row = raw as Partial<ReadingDetail> | null;
  if (!row || row.content_id !== source.content_id || row.content_type !== source.content_type || !Array.isArray(row.blocks)) throw new Error("正文响应无效");
  const types = ["paragraph", "heading", "quote", "code", "list-item", "image", "unsupported"];
  const blocks = row.blocks.slice(0, 4000).flatMap((block): ReadingBlock[] => {
    if (!block || !types.includes(block.type) || typeof block.text !== "string") return [];
    if (block.type === "image") {
      const url = readingImageUrl(block.url);
      return url ? [{ type: "image", text: block.text.slice(0, 200), url }] : [{ type: "unsupported", text: "此图片暂不可用，请在原文中查看。" }];
    }
    return [{ type: block.type, text: block.text, level: typeof block.level === "number" ? Math.max(1, Math.min(6, block.level)) : undefined }];
  });
  return { content_id: source.content_id, content_type: source.content_type, blocks, limited: row.limited === true,
    notices: Array.isArray(row.notices) ? row.notices.filter((notice): notice is string => typeof notice === "string").slice(0, 10) : [] };
}

const messages: Record<string, string> = {
  busy: "搜索、研究或账号操作正在进行，请结束后重试读取正文。",
  login_required: "请先在设置中登录知乎，再重试读取正文。",
  session_expired: "知乎登录已失效，请重新登录后重试。",
  rate_limited: "知乎触发验证码或访问限制，请在原平台检查后稍后重试。",
  restricted: "知乎暂未提供此内容的读取权限，请打开原文查看。",
  timeout: "正文读取超时，请重试或打开原文。",
};

export function readingError(error: unknown): string {
  const code = (error as { response?: { data?: { detail?: { code?: unknown } } } })?.response?.data?.detail?.code;
  return typeof code === "string" && messages[code] ? messages[code] : "暂时无法读取正文，请重试或打开原文。";
}
