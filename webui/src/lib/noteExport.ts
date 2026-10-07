import type { NoteDocument } from "./spaceNotes.js";

export function safeNoteUrl(value: unknown): string | null {
  if (typeof value !== "string" || /[\u0000-\u0020]/.test(value)) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function inline(node: NoteDocument): string {
  if (node.type === "hardBreak") return "  \n";
  if (node.type !== "text") return (node.content ?? []).map(inline).join("");
  let text = (node.text ?? "").replace(/([\\`*_{}[\]<>!#])/g, "\\$1").replace(/^(\s*\d+)\./gm, "$1\\.").replace(/^(\s*)([-+])(?=\s)/gm, "$1\\$2");
  const marks = node.marks ?? [];
  if (marks.some((mark) => mark.type === "bold")) text = text.replace(/^(\s*)(.*?)(\s*)$/s, "$1**$2**$3");
  if (marks.some((mark) => mark.type === "italic")) text = text.replace(/^(\s*)(.*?)(\s*)$/s, "$1*$2*$3");
  const href = safeNoteUrl(marks.find((mark) => mark.type === "link")?.attrs?.href);
  if (href) text = `[${text}](<${href.replace(/[<>]/g, (value) => encodeURIComponent(value))}>)`;
  return text;
}

export function noteMarkdown(node: NoteDocument): string {
  if (node.type === "text" || node.type === "hardBreak") return inline(node);
  if (node.type === "heading") return `${"#".repeat(Number(node.attrs?.level) || 1)} ${inline(node)}`;
  if (node.type === "paragraph") return inline(node);
  if (["bulletList", "orderedList", "taskList"].includes(node.type)) {
    return (node.content ?? []).map((item, index) => {
      const prefix = node.type === "orderedList" ? `${(Number(node.attrs?.start) || 1) + index}. `
        : node.type === "taskList" ? `- [${item.attrs?.checked ? "x" : " "}] ` : "- ";
      const content = noteMarkdown(item).split("\n");
      return prefix + content.map((line, row) => row ? (line ? " ".repeat(prefix.length) + line : "") : line).join("\n");
    }).join("\n");
  }
  return (node.content ?? []).map(noteMarkdown).join("\n\n");
}

export function noteExportFilename(name: string): string {
  const clean = name.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/[.\s]+$/g, "").slice(0, 80);
  return `${clean || "研究笔记"}.md`;
}
