/** Rich documents and a serial autosave queue, independent of the editor UI. */
export interface NoteDocument {
  type: string;
  text?: string;
  attrs?: Record<string, unknown>;
  marks?: { type: string; attrs?: Record<string, unknown> }[];
  content?: NoteDocument[];
}

export const EMPTY_NOTE: NoteDocument = { type: "doc", content: [{ type: "paragraph" }] };
export const NOTE_MAX_LENGTH = 50_000;
export const NOTE_MAX_BYTES = 2 * 1024 * 1024;
export const NOTE_FONT_SIZES = [14, 16, 18, 20, 24, 28, 32];

export function noteText(document: NoteDocument): string {
  if (document.type === "text") return document.text ?? "";
  if (document.type === "hardBreak") return "\n";
  const separator = ["doc", "listItem", "taskItem", "bulletList", "orderedList", "taskList"].includes(document.type) ? "\n" : "";
  return (document.content ?? []).map(noteText).join(separator);
}

export function noteWithinLimits(document: NoteDocument): boolean {
  return Array.from(noteText(document).replace(/\n/g, "")).length <= NOTE_MAX_LENGTH
    && new TextEncoder().encode(JSON.stringify(document)).length <= NOTE_MAX_BYTES;
}

export function spaceError(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return error instanceof Error ? error.message : "保存失败，请重试";
}

type SaveNote = (document: NoteDocument, revision: number) => Promise<{ note_revision: number }>;

export class NoteSession {
  document: NoteDocument;
  revision: number;
  dirty = false;
  saving = false;
  error = "";
  conflict = false;
  private generation = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private flight?: Promise<boolean>;
  private disposed = false;
  private appendedResearch = new Set<string>();

  constructor(document: NoteDocument, revision: number, private save: SaveNote, private changed: () => void) {
    this.document = document;
    this.revision = revision;
  }

  change(document: NoteDocument, composing = false): void {
    if (this.disposed) return;
    this.document = document;
    this.generation += 1;
    this.dirty = true;
    clearTimeout(this.timer);
    if (!this.conflict) this.error = "";
    if (!noteWithinLimits(document)) this.error = "笔记最多 50000 字，格式数据最多 2 MB；草稿已保留，请缩短后保存";
    if (!composing && !this.error) this.timer = setTimeout(() => { void this.flush(); }, 600);
    this.changed();
  }

  flush(): Promise<boolean> {
    clearTimeout(this.timer);
    if (this.disposed) return Promise.resolve(true);
    if (this.flight) return this.flight;
    if (!this.dirty) return Promise.resolve(true);
    if (this.conflict || !noteWithinLimits(this.document)) return Promise.resolve(false);
    this.flight = this.drain().finally(() => { this.flight = undefined; });
    return this.flight;
  }

  private async drain(): Promise<boolean> {
    this.saving = true;
    this.error = "";
    this.changed();
    try {
      while (this.dirty) {
        if (!noteWithinLimits(this.document)) {
          this.error = "笔记超出长度限制；草稿已保留，请缩短后保存";
          return false;
        }
        const generation = this.generation;
        const document = this.document;
        const saved = await this.save(document, this.revision);
        this.revision = saved.note_revision;
        if (generation === this.generation) this.dirty = false;
      }
      return true;
    } catch (error) {
      this.error = spaceError(error);
      this.conflict = (error as { response?: { status?: number } })?.response?.status === 409;
      return false;
    } finally {
      this.saving = false;
      this.changed();
    }
  }

  resolveWithRevision(revision: number): void {
    this.revision = revision;
    this.conflict = false;
    this.error = "";
    this.changed();
  }

  cancelScheduledSave(): void { clearTimeout(this.timer); }

  hasResearch(jobId: string): boolean { return this.appendedResearch.has(jobId); }

  appendResearch(jobId: string, generated: NoteDocument, apply: (content: NoteDocument[]) => boolean): boolean {
    if (this.disposed || this.conflict || this.appendedResearch.has(jobId)) return false;
    const combined = { ...this.document, content: [...(this.document.content ?? []), ...(generated.content ?? [])] };
    if (!noteWithinLimits(combined)) throw new Error("笔记最多 50000 字，格式数据最多 2 MB；预览已保留，请缩短后追加");
    if (!apply(generated.content ?? [])) return false;
    this.appendedResearch.add(jobId);
    this.changed();
    return true;
  }

  dispose(): void { this.disposed = true; clearTimeout(this.timer); }

  sync(document: NoteDocument, revision: number): boolean {
    if (this.dirty || this.saving || revision <= this.revision) return false;
    this.document = document;
    this.revision = revision;
    this.changed();
    return true;
  }
}
