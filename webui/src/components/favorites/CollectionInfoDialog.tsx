import { useEffect, useRef, useState } from "react";
import { ImagePlus, X } from "lucide-react";
import type { LibraryCollection } from "@/lib/libraryApi";

export interface CollectionInfoDraft {
  name: string;
  description: string;
  coverData?: string;
  removeCover?: boolean;
}

export function CollectionInfoDialog({ collection, fallbackCover, busy, onSave, onCreate, onClose }: {
  collection: LibraryCollection | null;
  fallbackCover: string | null;
  busy: boolean;
  onSave: (id: number, draft: CollectionInfoDraft) => Promise<boolean>;
  onCreate?: (draft: CollectionInfoDraft) => Promise<boolean>;
  onClose: () => void;
}) {
  const creating = collection === null;
  const [name, setName] = useState(collection?.name ?? "");
  const [description, setDescription] = useState(collection?.description ?? "");
  const [coverData, setCoverData] = useState<string | null>(null);
  const [removeCover, setRemoveCover] = useState(false);
  const [fileError, setFileError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const coverSrc = coverData ?? (removeCover ? null : collection?.coverUrl) ?? fallbackCover;

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [busy, onClose]);

  const selectCover = (file: File | undefined) => {
    setFileError("");
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setFileError("请选择图片文件");
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setFileError("图片不能超过 5 MB");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== "string") {
        setFileError("图片读取失败，请重试");
        return;
      }
      setCoverData(reader.result);
      setRemoveCover(false);
    };
    reader.onerror = () => setFileError("图片读取失败，请重试");
    reader.readAsDataURL(file);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim() || busy) return;
    const draft = {
      name: name.trim(), description: description.trim(),
      ...(coverData ? { coverData } : {}),
      ...(removeCover ? { removeCover: true } : {}),
    };
    const ok = creating ? await onCreate?.(draft) : await onSave(collection.id, draft);
    if (ok) onClose();
  };

  return <div className="folder-info-overlay" onPointerDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div className="folder-info-card" ref={card} role="dialog" aria-modal="true" aria-labelledby="folder-info-title" onKeyDown={(event) => {
      if (event.key !== "Tab") return;
      const controls = card.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)');
      if (!controls?.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }}>
      <div className="folder-info-top"><h2 id="folder-info-title">{creating ? "新建收藏夹" : "编辑收藏夹信息"}</h2><button type="button" className="folder-info-close" aria-label="关闭编辑卡片" disabled={busy} onClick={onClose}><X aria-hidden="true" /></button></div>
      <form onSubmit={(event) => void save(event)}>
        <button type="button" className="folder-info-cover" aria-label="点击更换收藏夹封面" disabled={busy} onClick={() => fileInput.current?.click()}>
          {coverSrc ? <img src={coverSrc} alt="当前收藏夹封面" /> : <span className="folder-info-cover-empty"><ImagePlus aria-hidden="true" />选择封面</span>}
          <span className="folder-info-cover-action">点击更换封面</span>
        </button>
        <input ref={fileInput} className="sr-only" type="file" accept="image/*" tabIndex={-1} onChange={(event) => { selectCover(event.target.files?.[0]); event.target.value = ""; }} />
        {(collection?.coverUrl || coverData) && <button type="button" className="folder-info-reset-cover" disabled={busy} onClick={() => { setCoverData(null); setRemoveCover(!creating); setFileError(""); }}>{creating ? "取消选择封面" : "恢复自动封面"}</button>}
        {fileError && <p className="folder-info-error" role="alert">{fileError}</p>}
        <label className="folder-info-field" htmlFor="folder-info-name">名称 <span aria-hidden="true">*</span><input id="folder-info-name" className="field" autoFocus maxLength={60} value={name} onChange={(event) => setName(event.target.value)} /><small>{name.length}/60</small></label>
        <label className="folder-info-field" htmlFor="folder-info-description">简介<textarea id="folder-info-description" className="field" maxLength={200} rows={4} placeholder="简单描述这个收藏夹" value={description} onChange={(event) => setDescription(event.target.value)} /><small>{description.length}/200</small></label>
        <div className="folder-info-actions"><button type="button" className="btn" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="btn primary" disabled={busy || !name.trim() || !!fileError}>{busy ? (creating ? "正在创建" : "正在保存") : creating ? "创建" : "保存"}</button></div>
      </form>
    </div>
  </div>;
}
