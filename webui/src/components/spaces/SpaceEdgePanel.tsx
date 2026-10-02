import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Pin, PinOff, X } from "lucide-react";
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
  return createPortal(<section ref={root} hidden={hidden} className={`space-edge space-edge-${side} ${open ? "is-open" : ""}`} style={{ "--space-edge-height": `${height}px` } as CSSProperties}>
    <button ref={trigger} type="button" className="space-edge-trigger" aria-label={triggerLabel ?? label} aria-controls={id} aria-expanded={open} aria-hidden={open} tabIndex={open ? -1 : 0} disabled={disabled} onClick={() => onOpenChange(true)}>{icon}<span>{label}</span></button>
    <div ref={surface} id={id} className="space-edge-surface" aria-hidden={!open}>
      <div className="space-edge-head"><h2>{label}</h2><div>
        <button type="button" className="btn small" aria-label={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} aria-pressed={pinned} title={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} onClick={() => onPinChange(!pinned)}>{pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}</button>
        <button type="button" className="btn small" aria-label={closeLabel} onClick={close}><X aria-hidden="true" /></button>
      </div></div>
      {children}
    </div>
  </section>, document.body);
}
