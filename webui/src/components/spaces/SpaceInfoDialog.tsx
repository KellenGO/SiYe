import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { useSpaces } from "@/hooks/useSpaces";
import type { ResearchSpace } from "@/lib/spacesApi";

export function SpaceInfoDialog({ space, initialName = "", onClose }: { space?: ResearchSpace; initialName?: string; onClose: () => void }) {
  const { t } = useTranslation();
  const spaces = useSpaces();
  const [name, setName] = useState(space?.name ?? initialName);
  const [description, setDescription] = useState(space?.description ?? "");
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => { previous?.isConnected && previous.focus(); };
  }, []);
  return createPortal(<div className="confirm-overlay" onPointerDown={(event) => { if (event.target === event.currentTarget && !spaces.busy) onClose(); }}>
    <div ref={panel} className="confirm-card space-info-card" role="dialog" aria-modal="true" aria-labelledby={id} onKeyDown={(event) => {
      if (event.key === "Escape" && !spaces.busy) { event.stopPropagation(); onClose(); }
      if (event.key === "Tab") {
        const controls = [...panel.current!.querySelectorAll<HTMLElement>('input, textarea, button:not(:disabled)')];
        const target = event.shiftKey ? controls[controls.length - 1] : controls[0];
        if (document.activeElement === (event.shiftKey ? controls[0] : controls[controls.length - 1])) { event.preventDefault(); target.focus(); }
      }
    }}>
      <h2 id={id}>{t(space ? "spaces.edit" : "spaces.create")}</h2>
      <form onSubmit={async (event) => {
        event.preventDefault();
        const saved = space ? await spaces.update(space.id, name, description) : await spaces.create(name, description);
        if (saved) onClose();
      }}>
        <label>{t("spaces.name")}<input ref={input} className="field" value={name} maxLength={60} required onChange={(event) => setName(event.target.value)} placeholder={t("spaces.nameExample")} /></label>
        <label>{t("spaces.description")}<textarea className="field" rows={3} maxLength={200} value={description} onChange={(event) => setDescription(event.target.value)} placeholder={t("spaces.descriptionExample")} /></label>
        <div className="confirm-actions"><button type="button" className="btn" disabled={spaces.busy} onClick={onClose}>{t("spaces.cancel")}</button><button className="btn primary" disabled={spaces.busy || !name.trim()}>{t(space ? "spaces.save" : "spaces.createAndActivate")}</button></div>
      </form>
    </div>
  </div>, document.body);
}
