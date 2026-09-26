import { useRef, useState } from "react";
import { toast } from "sonner";
import type { BookmarkLibrary } from "@/hooks/useBookmarks";
import { MAX_BACKUP_BYTES } from "@/lib/bookmarks";
import { TOOL_BUTTON } from "./ResultTools";

/** 备份 / 恢复本地收藏库（数据来自后端 /api/library，不再读浏览器 localStorage）。 */
export function BookmarkBackup({ library }: { library: BookmarkLibrary }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const download = async () => {
    setBusy(true);
    try {
      const contents = await library.exportBackup();
      if (contents === null) return;
      const url = URL.createObjectURL(new Blob([contents], { type: "application/json;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `收藏备份-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "备份失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  };

  const restore = async (file: File) => {
    setBusy(true);
    try {
      if (file.size > MAX_BACKUP_BYTES) throw new Error("收藏备份不能超过 64 MB");
      await library.importBackup(await file.text());
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "无法读取备份文件");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap gap-2">
        <button type="button" className={TOOL_BUTTON} disabled={!!library.error || busy} onClick={() => void download()}>
          {busy ? "处理中…" : "导出全部收藏"}
        </button>
        <button type="button" className={TOOL_BUTTON} disabled={!!library.error || busy} onClick={() => input.current?.click()}>
          {busy ? "正在导入…" : "导入收藏备份"}
        </button>
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          className="hidden"
          aria-label="选择收藏备份文件"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void restore(file);
          }}
        />
      </div>
      <p className="text-xs text-cyber-text-muted">
        导出的 JSON 包含全部收藏、收藏夹归属、简介、封面和已保存备注。导入时只合并新增，重复内容保留现有备注；最多 500 条，文件不超过 64 MB。
      </p>
    </div>
  );
}
