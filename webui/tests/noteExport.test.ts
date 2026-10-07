import { test } from "node:test";
import assert from "node:assert/strict";
import { noteMarkdown, noteExportFilename, safeNoteUrl } from "../src/lib/noteExport.js";
import type { NoteDocument } from "../src/lib/spaceNotes.js";

const text = (value: string, marks: NoteDocument["marks"] = []): NoteDocument => ({ type: "text", text: value, marks });
const paragraph = (...content: NoteDocument[]): NoteDocument => ({ type: "paragraph", content });

test("Markdown export preserves headings, formatting, source links and nested lists", () => {
  const note: NoteDocument = { type: "doc", content: [
    { type: "heading", attrs: { level: 2 }, content: [text("研究结论")] },
    paragraph(text("重点", [{ type: "bold" }]), text("与"), text("补充", [{ type: "italic" }]), text(" [S1:body]", [{ type: "link", attrs: { href: "https://example.com/a(b)" } }])),
    { type: "orderedList", attrs: { start: 3 }, content: [{ type: "listItem", content: [paragraph(text("第一项")),
      { type: "bulletList", content: [{ type: "listItem", content: [paragraph(text("子项"))] }] }] }] },
    { type: "taskList", content: [{ type: "taskItem", attrs: { checked: true }, content: [paragraph(text("完成"))] },
      { type: "taskItem", attrs: { checked: false }, content: [paragraph(text("待做"))] }] },
  ] };
  assert.equal(noteMarkdown(note), "## 研究结论\n\n**重点**与*补充*[ \\[S1:body\\]](<https://example.com/a(b)>)\n\n3. 第一项\n\n   - 子项\n\n- [x] 完成\n- [ ] 待做");
});

test("literal Markdown is escaped, line breaks retained and unsafe links omitted", () => {
  assert.equal(noteMarkdown(paragraph(text("*原文* <script>"), { type: "hardBreak" }, text("- 原文"))), "\\*原文\\* \\<script\\>  \n\\- 原文");
  assert.equal(noteMarkdown(text("危险", [{ type: "link", attrs: { href: "javascript:alert(1)" } }])), "危险");
  for (const url of ["file:///C:/data", "javascript:alert(1)", "https://user:pass@example.com", "https://example.com\n"]) assert.equal(safeNoteUrl(url), null);
  assert.equal(safeNoteUrl("https://example.com"), "https://example.com/");
  assert.equal(noteExportFilename('护肤/研究:*'), "护肤_研究__.md");
});
