import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { MoveDiagonal2, Pin, PinOff, X } from "lucide-react";
import { useTranslation } from "react-i18next";

export function SpaceEdgePanel({ side, label, triggerLabel, icon, open, pinned, disabled = false, hidden = false, closeLabel, onOpenChange, onPinChange, children }: {
  side: "left" | "right"; label: string; triggerLabel?: string; icon: ReactNode; open: boolean; pinned: boolean; disabled?: boolean; hidden?: boolean;
  closeLabel: string; onOpenChange: (open: boolean) => void; onPinChange: (pinned: boolean) => void; children: ReactNode;
}) {
  const { t } = useTranslation();
  const root = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);
  const [resizing, setResizing] = useState(false);
  const [size, setSize] = useState<{ width: number; height: number } | null>(() => {
    try {
      const value = JSON.parse(localStorage.getItem("siye-note-size") ?? "null");
      return Number.isFinite(value?.width) && Number.isFinite(value?.height) ? value : null;
    } catch { return null; }
  });
  const drag = useRef<{ pointer: number; x: number; y: number; width: number; height: number } | null>(null);
  const resize = (width: number, nextHeight: number) => {
    const right = root.current?.getBoundingClientRect().right ?? window.innerWidth - 8;
    const top = root.current?.getBoundingClientRect().top ?? 112;
    const next = { width: Math.max(220, Math.min(width, right - 8)), height: Math.max(300, Math.min(nextHeight, window.innerHeight - top - 24)) };
    setSize(next);
    try { localStorage.setItem("siye-note-size", JSON.stringify(next)); } catch { /* Current size still works. */ }
  };
  const id = useId();
  const close = useCallback(() => {
    onPinChange(false);
    onOpenChange(false);
    requestAnimationFrame(() => trigger.current?.focus());
  }, [onOpenChange, onPinChange]);
  useLayoutEffect(() => {
    const element = surface.current;
    if (!element) return;
    const measure = () => {
      const next = Math.ceil(element.getBoundingClientRect().height) + 2;
      if (next > 2) setHeight(next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [hidden]);
  useLayoutEffect(() => {
    if (!surface.current) return;
    surface.current.inert = !open;
    if (open && document.activeElement === trigger.current) surface.current.querySelector<HTMLElement>("button:not(:disabled), select:not(:disabled)")?.focus();
  }, [open]);
  useEffect(() => {
    const element = root.current;
    if (!element || hidden || disabled) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clear = () => { clearTimeout(timer); };
    const hover = () => window.matchMedia("(hover: hover) and (min-width: 1001px)").matches;
    const enter = () => { clear(); if (hover()) onOpenChange(true); };
    const leave = () => {
      clear();
      if (!hover() || pinned) return;
      timer = setTimeout(() => {
        if (!element.matches(":hover") && !element.contains(document.activeElement)) onOpenChange(false);
      }, 220);
    };
    const keyboard = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest("dialog[open]")) return;
      if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); }
    };
    element.addEventListener("keydown", keyboard);
    element.addEventListener("pointerenter", enter);
    element.addEventListener("pointerleave", leave);
    element.addEventListener("focusout", leave);
    return () => {
      clear();
      element.removeEventListener("keydown", keyboard);
      element.removeEventListener("pointerenter", enter);
      element.removeEventListener("pointerleave", leave);
      element.removeEventListener("focusout", leave);
    };
  }, [disabled, hidden, open, pinned, onOpenChange, close]);
  return createPortal(<section ref={root} hidden={hidden} className={`space-edge space-edge-${side} ${open ? "is-open" : ""} ${resizing ? "is-resizing" : ""}`} style={{ "--space-edge-height": `${height}px`, ...(side === "right" && size ? { "--space-note-width": `${size.width}px`, "--space-note-height": `${size.height}px` } : {}) } as CSSProperties}>
    <button ref={trigger} type="button" className="space-edge-trigger" aria-label={triggerLabel ?? label} aria-controls={id} aria-expanded={open} aria-hidden={open} tabIndex={open ? -1 : 0} disabled={disabled} onClick={() => onOpenChange(true)}>{icon}<span>{label}</span></button>
    <div ref={surface} id={id} className="space-edge-surface" aria-hidden={!open}>
      <div className="space-edge-head"><h2>{label}</h2><div>
        <button type="button" className="btn small" aria-label={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} aria-pressed={pinned} title={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} onClick={() => onPinChange(!pinned)}>{pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}</button>
        <button type="button" className="btn small" aria-label={closeLabel} onClick={close}><X aria-hidden="true" /></button>
      </div></div>
      {children}
    </div>
    {side === "right" && open && <button type="button" className="space-note-resize" aria-label={t("spaces.resizeNote")} title={t("spaces.resizeNoteHint")}
      onPointerDown={(event) => {
        if (event.button !== 0 || !root.current) return;
        event.preventDefault(); onPinChange(true); setResizing(true);
        const rect = root.current.getBoundingClientRect();
        drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, width: rect.width, height: rect.height };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (start?.pointer === event.pointerId) resize(start.width + start.x - event.clientX, start.height + event.clientY - start.y);
      }}
      onPointerUp={(event) => { if (drag.current?.pointer === event.pointerId) { drag.current = null; setResizing(false); event.currentTarget.releasePointerCapture(event.pointerId); } }}
      onLostPointerCapture={() => { drag.current = null; setResizing(false); }}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].includes(event.key) || !root.current) return;
        event.preventDefault(); onPinChange(true);
        if (event.key === "Home") { setSize(null); try { localStorage.removeItem("siye-note-size"); } catch { /* Default size still works. */ } return; }
        const rect = root.current.getBoundingClientRect();
        resize(rect.width + (event.key === "ArrowLeft" ? 24 : event.key === "ArrowRight" ? -24 : 0), rect.height + (event.key === "ArrowDown" ? 24 : event.key === "ArrowUp" ? -24 : 0));
      }}><MoveDiagonal2 aria-hidden="true" /></button>}
  </section>, document.body);
}
