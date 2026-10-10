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
  media: { url: string; label: string }[];
  poster?: string;
  portrait: boolean;
}

export function supportsReading(source: Pick<UnifiedSearchResult, "platform" | "content_type" | "content_id" | "url">): boolean {
  try {
    const url = new URL(source.url);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    const path = url.pathname.replace(/\/$/, "");
    const identity = source.content_id;
    if (source.platform === "xhs") return ["note", "video"].includes(source.content_type) && /^[a-fA-F0-9]{24}$/.test(identity)
      && ["www.xiaohongshu.com", "xiaohongshu.com"].includes(url.hostname) && [`/explore/${identity}`, `/discovery/item/${identity}`].includes(path);
    if (source.platform === "douyin") return ["video", "note"].includes(source.content_type) && /^[0-9]{1,30}$/.test(identity)
      && ["www.douyin.com", "douyin.com"].includes(url.hostname) && [`/video/${identity}`, `/note/${identity}`].includes(path);
    if (source.platform === "bilibili") return source.content_type === "video" && ["www.bilibili.com", "bilibili.com"].includes(url.hostname)
      && (/^BV[A-Za-z0-9]{10}$/.test(identity) && path === `/video/${identity}` || /^[0-9]{1,20}$/.test(identity) && path === `/video/av${identity}`);
    if (source.platform !== "zhihu" || !/^[0-9]{1,30}$/.test(identity)) return false;
    if (source.content_type === "article") return url.hostname === "zhuanlan.zhihu.com" && url.pathname.replace(/\/$/, "") === `/p/${source.content_id}`;
    const match = url.pathname.match(/^\/(?:question\/[0-9]+\/)?answer\/([0-9]+)\/?$/);
    return source.content_type === "answer" && ["zhihu.com", "www.zhihu.com"].includes(url.hostname) && match?.[1] === source.content_id;
  } catch { return false; }
}

export function readingImageUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  if (readingMediaUrl(value)) return value;
  try {
    const url = new URL(value.startsWith("//") ? `https:${value}` : value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port || !(url.hostname === "zhimg.com" || url.hostname.endsWith(".zhimg.com"))) return null;
    url.protocol = "https:";
    return url.href;
  } catch { return null; }
}

export function readingMediaUrl(value: unknown): string | null {
  return typeof value === "string" && /^\/api\/reading\/media\/[A-Za-z0-9_-]{32}$/.test(value) ? value : null;
}

export function decodeReadingDetail(raw: unknown, source: Pick<UnifiedSearchResult, "content_id" | "content_type"> & Partial<Pick<UnifiedSearchResult, "platform">>): ReadingDetail {
  const row = raw as Partial<ReadingDetail> & { platform?: string } | null;
  if (!row || (row.platform ?? "zhihu") !== (source.platform ?? "zhihu") || row.content_id !== source.content_id || row.content_type !== source.content_type || !Array.isArray(row.blocks)) throw new Error("正文响应无效");
  const types = ["paragraph", "heading", "quote", "code", "list-item", "image", "unsupported"];
  const blocks = row.blocks.slice(0, 4000).flatMap((block): ReadingBlock[] => {
    if (!block || !types.includes(block.type) || typeof block.text !== "string") return [];
    if (block.type === "image") {
      const url = readingImageUrl(block.url);
      return url ? [{ type: "image", text: block.text.slice(0, 200), url }] : [{ type: "unsupported", text: "此图片暂不可用，请在原文中查看。" }];
    }
    return [{ type: block.type, text: block.text, level: typeof block.level === "number" ? Math.max(1, Math.min(6, block.level)) : undefined }];
  });
  const media = Array.isArray(row.media) ? row.media.slice(0, 16).flatMap((segment) => {
    const url = readingMediaUrl(segment?.url);
    return url ? [{ url, label: typeof segment.label === "string" ? segment.label.slice(0, 80) : "视频" }] : [];
  }) : [];
  return { content_id: source.content_id, content_type: source.content_type, blocks, limited: row.limited === true, media,
    poster: readingImageUrl(row.poster) ?? undefined, portrait: row.portrait === true,
    notices: Array.isArray(row.notices) ? row.notices.filter((notice): notice is string => typeof notice === "string").slice(0, 10) : [] };
}

const messages: Record<string, string> = {
  busy: "搜索、研究或账号操作正在进行，请结束后重试读取正文。",
  login_required: "请先在设置中登录此平台，再重试读取内容。",
  session_expired: "平台登录已失效，请重新登录后重试。",
  rate_limited: "平台触发验证码或访问限制，请在原平台检查后稍后重试。",
  restricted: "平台暂未提供此内容的读取权限，请打开原文查看。",
  timeout: "正文读取超时，请重试或打开原文。",
};

export function readingError(error: unknown): string {
  const code = (error as { response?: { data?: { detail?: { code?: unknown } } } })?.response?.data?.detail?.code;
  return typeof code === "string" && messages[code] ? messages[code] : "暂时无法读取正文，请重试或打开原文。";
}

export interface ReadingComments {
  entries: { id: string; parent_id: string | null; author: string; text: string; like_count?: number }[];
  limited: boolean;
  sort: string;
  notice: string;
}

export function decodeReadingComments(raw: unknown, source: Pick<UnifiedSearchResult, "platform" | "content_id" | "content_type">): ReadingComments {
  const row = raw as Partial<ReadingComments> & Partial<UnifiedSearchResult> | null;
  if (!row || row.platform !== source.platform || row.content_id !== source.content_id || row.content_type !== source.content_type || !Array.isArray(row.entries)) throw new Error("评论响应无效");
  return { entries: row.entries.slice(0, 50).flatMap((entry) => {
    if (!entry || typeof entry.id !== "string" || typeof entry.text !== "string") return [];
    return [{ id: entry.id, parent_id: typeof entry.parent_id === "string" ? entry.parent_id : null,
      text: entry.text.slice(0, 12000), author: typeof entry.author === "string" ? entry.author.slice(0, 200) : "",
      like_count: typeof entry.like_count === "number" && Number.isFinite(entry.like_count) ? Math.max(0, entry.like_count) : undefined }];
  }), limited: row.limited === true, sort: typeof row.sort === "string" ? row.sort.slice(0, 30) : "平台默认",
    notice: typeof row.notice === "string" ? row.notice.slice(0, 500) : "" };
}
