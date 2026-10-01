import { test } from "node:test";
import assert from "node:assert/strict";
import { EMPTY_NOTE, NoteSession, noteText, noteWithinLimits, type NoteDocument } from "../src/lib/spaceNotes.js";
import { spaceSources } from "../src/lib/spacesApi.js";
import type { UnifiedSearchResult } from "../src/types/search.js";

const doc = (text: string): NoteDocument => ({ type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text }] }] });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

test("autosave serializes edits arriving during a save without overwriting the newest draft", async () => {
  const first = deferred<{ note_revision: number }>();
  const writes: { text: string; revision: number }[] = [];
  const session = new NoteSession(EMPTY_NOTE, 0, async (document, revision) => {
    writes.push({ text: noteText(document), revision });
    return writes.length === 1 ? first.promise : { note_revision: 2 };
  }, () => {});
  session.change(doc("第一版"));
  const saved = session.flush();
  session.change(doc("最新版本"));
  assert.equal(session.flush(), saved);
  first.resolve({ note_revision: 1 });
  assert.equal(await saved, true);
  assert.equal(JSON.stringify(writes), JSON.stringify([{ text: "第一版", revision: 0 }, { text: "最新版本", revision: 1 }]));
  assert.equal(noteText(session.document), "最新版本");
  assert.equal(session.dirty, false);
  session.dispose();
});

test("failed saves keep the draft, can retry, and a different space keeps its own revision", async () => {
  let fail = true;
  const a = new NoteSession(EMPTY_NOTE, 4, async (_document, revision) => {
    if (fail) throw new Error("暂时离线");
    return { note_revision: revision + 1 };
  }, () => {});
  const b = new NoteSession(EMPTY_NOTE, 0, async () => ({ note_revision: 1 }), () => {});
  a.change(doc("苏州")); b.change(doc("杭州"));
  assert.equal(await a.flush(), false);
  assert.equal(a.dirty, true);
  assert.equal(noteText(a.document), "苏州");
  assert.equal(await b.flush(), true);
  fail = false;
  assert.equal(await a.flush(), true);
  assert.equal(a.revision, 5);
  assert.equal(b.revision, 1);
  a.dispose(); b.dispose();
});

test("conflicts require explicit revision resolution and never replace local text", async () => {
  let conflict = true;
  const session = new NoteSession(EMPTY_NOTE, 0, async (_document, revision) => {
    if (conflict) throw { response: { status: 409, data: { detail: "并发修改" } } };
    return { note_revision: revision + 1 };
  }, () => {});
  session.change(doc("本地草稿"));
  assert.equal(await session.flush(), false);
  assert.equal(session.conflict, true);
  assert.equal(session.sync(doc("服务器最新版本"), 9), false);
  assert.equal(await session.flush(), false);
  conflict = false;
  session.resolveWithRevision(9);
  assert.equal(await session.flush(), true);
  assert.equal(session.revision, 10);
  assert.equal(noteText(session.document), "本地草稿");
  session.dispose();
});

test("plain text extraction retains list lines and note limits reject rather than truncate", () => {
  const note: NoteDocument = { type: "doc", content: [{ type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "攻略" }] }, { type: "taskList", content: [{ type: "taskItem", attrs: { checked: true }, content: [{ type: "paragraph", content: [{ type: "text", text: "订票" }] }] }] }] };
  assert.equal(noteText(note), "攻略\n订票");
  assert.equal(noteWithinLimits(doc("😀".repeat(50_000))), true);
  assert.equal(noteWithinLimits(doc("x".repeat(50_001))), false);
});

test("adding a grouped search card expands the visible versions and strips private fields", () => {
  const a: UnifiedSearchResult = { platform: "xhs", content_id: "one", title: "攻略", url: "https://www.xiaohongshu.com/explore/one", content_type: "note", cover_url: null, author: null, published_at: null, metrics: {}, rank: 1 };
  const b: UnifiedSearchResult = { ...a, platform: "zhihu", url: "https://www.zhihu.com/question/one" };
  const sources = spaceSources({ ...a, grouped_sources: [a, b] });
  assert.equal(sources.length, 2);
  assert.equal(sources[1].platform, "zhihu");
  assert.equal(sources[0].grouped_sources, null);
  assert.equal(spaceSources(a).length, 1);
});

test("discarding a space disposes scheduled saves", async () => {
  let writes = 0;
  const session = new NoteSession(EMPTY_NOTE, 0, async () => { writes++; return { note_revision: 1 }; }, () => {});
  session.change(doc("丢弃的笔记"));
  session.dispose();
  assert.equal(await session.flush(), true);
  assert.equal(writes, 0);
});
