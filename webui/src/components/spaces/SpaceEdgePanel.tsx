import { useEffect, useRef, type ReactNode } from "react";
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
  const id = `space-edge-${side}`;
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
      if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); onPinChange(false); onOpenChange(false); trigger.current?.focus(); }
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
  }, [disabled, hidden, open, pinned, onOpenChange, onPinChange]);
  const close = () => { onPinChange(false); onOpenChange(false); trigger.current?.focus(); };
  return createPortal(<section ref={root} hidden={hidden} className={`space-edge space-edge-${side} ${open ? "is-open" : ""}`}>
    <button ref={trigger} type="button" className="space-edge-trigger" aria-label={triggerLabel ?? label} aria-controls={id} aria-expanded={open} disabled={disabled} onClick={() => onOpenChange(true)}>{icon}<span>{label}</span>{pinned && <Pin className="space-edge-pin-indicator" aria-hidden="true" />}</button>
    <div id={id} className="space-edge-surface" hidden={!open}>
      <div className="space-edge-head"><h2>{label}</h2><div>
        <button type="button" className="btn small" aria-label={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} aria-pressed={pinned} title={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} onClick={() => onPinChange(!pinned)}>{pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}</button>
        <button type="button" className="btn small" aria-label={closeLabel} onClick={close}><X aria-hidden="true" /></button>
      </div></div>
      {children}
    </div>
  </section>, document.body);
}
