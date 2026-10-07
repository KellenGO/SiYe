import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Columns2, Maximize2, Minimize2, Pin, PinOff, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { WorkspaceSplitLayout } from "./WorkspaceContext";

export function SpaceEdgePanel({ side, label, triggerLabel, icon, open, pinned, disabled = false, hidden = false, closeLabel, onOpenChange, onPinChange, split, children }: {
  side: "left" | "right"; label: string; triggerLabel?: string; icon: ReactNode; open: boolean; pinned: boolean; disabled?: boolean; hidden?: boolean;
  closeLabel: string; onOpenChange: (open: boolean) => void; onPinChange: (pinned: boolean) => void; split?: WorkspaceSplitLayout; children: ReactNode;
}) {
  const { t } = useTranslation();
  const root = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);
  const [resizing, setResizing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 1000px)").matches);
  const [size, setSize] = useState<{ width: number; height: number } | null>(() => {
    try {
      const value = JSON.parse(localStorage.getItem("siye-note-size") ?? "null");
      return Number.isFinite(value?.width) && Number.isFinite(value?.height) ? value : null;
    } catch { return null; }
  });
  const drag = useRef<{ pointer: number; x: number; y: number; width: number; height: number; axis: "width" | "height" | "both" } | null>(null);
  const splitActive = side === "right" && Boolean(split?.active);
  const splitHistoryOpen = split?.historyOpen;
  const restoreSplit = split?.restore;
  const changeSplitHistory = split?.setHistoryOpen;
  const fullscreen = side === "right" && open && !hidden && expanded && !narrow && !splitActive;
  const resize = (width: number, nextHeight: number) => {
    const sidebar = document.body.classList.contains("has-ai-sidebar") ? parseFloat(getComputedStyle(document.body).getPropertyValue("--research-panel-width")) || 560 : 0;
    const right = fullscreen ? window.innerWidth - sidebar - 8 : root.current?.getBoundingClientRect().right ?? window.innerWidth - 8;
    const top = fullscreen ? 112 : root.current?.getBoundingClientRect().top ?? 112;
    const next = { width: Math.max(220, Math.min(width, right - 8)), height: Math.max(300, Math.min(nextHeight, window.innerHeight - top - 24)) };
    setExpanded(false);
    setSize(next);
    try { localStorage.setItem("siye-note-size", JSON.stringify(next)); } catch { /* Current size still works. */ }
  };
  const id = useId();
  const close = useCallback(() => {
    setExpanded(false);
    onPinChange(false);
    onOpenChange(false);
    requestAnimationFrame(() => trigger.current?.focus());
  }, [onOpenChange, onPinChange]);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1000px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!fullscreen) return;
    document.body.classList.add("has-note-expanded");
    document.body.dataset.notePanelOwner = id;
    return () => {
      if (document.body.dataset.notePanelOwner === id) {
        document.body.classList.remove("has-note-expanded"); delete document.body.dataset.notePanelOwner;
      }
    };
  }, [fullscreen, id]);
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
      if (!hover() || pinned || fullscreen || splitActive) return;
      timer = setTimeout(() => {
        if (!element.matches(":hover") && !element.contains(document.activeElement)) onOpenChange(false);
      }, 220);
    };
    const keyboard = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest("dialog[open]")) return;
      if (event.key === "Escape" && open) {
        event.preventDefault(); event.stopPropagation();
        if (splitActive) { if (splitHistoryOpen) changeSplitHistory?.(false); else restoreSplit?.(); }
        else if (fullscreen) setExpanded(false); else close();
      }
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
  }, [disabled, hidden, open, pinned, fullscreen, splitActive, splitHistoryOpen, restoreSplit, changeSplitHistory, onOpenChange, close]);
  const startResize = (event: ReactPointerEvent<HTMLDivElement>, axis: "width" | "height" | "both") => {
    if (event.button !== 0 || !root.current) return;
    event.preventDefault(); onPinChange(true); setResizing(true);
    const rect = root.current.getBoundingClientRect();
    const defaultWidth = Math.max(220, Math.min(360, (window.innerWidth - 960) / 2 - 56));
    drag.current = { pointer: event.pointerId, x: event.clientX, y: event.clientY, axis,
      width: fullscreen && axis === "height" ? size?.width ?? defaultWidth : rect.width,
      height: fullscreen && axis === "width" ? size?.height ?? Math.min(760, window.innerHeight - 138) : surface.current?.getBoundingClientRect().height ?? rect.height };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (start?.pointer === event.pointerId) resize(start.width + (start.axis === "height" ? 0 : start.x - event.clientX), start.height + (start.axis === "width" ? 0 : event.clientY - start.y));
  };
  const endResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointer === event.pointerId) { drag.current = null; setResizing(false); event.currentTarget.releasePointerCapture(event.pointerId); }
  };
  const resizeHandle = (axis: "width" | "height" | "both") => <div className={`space-note-resize space-note-resize-${axis}`} role="separator" tabIndex={axis === "both" ? -1 : 0}
    aria-orientation={axis === "height" ? "horizontal" : "vertical"} aria-label={t(axis === "height" ? "spaces.resizeNoteHeight" : axis === "both" ? "spaces.resizeNoteBoth" : "spaces.resizeNote")}
    title={t("spaces.resizeNoteHint")} onPointerDown={(event) => startResize(event, axis)} onPointerMove={moveResize} onPointerUp={endResize}
    onLostPointerCapture={() => { drag.current = null; setResizing(false); }}
    onKeyDown={(event) => {
      const keys = axis === "height" ? ["ArrowUp", "ArrowDown", "Home", "End"] : ["ArrowLeft", "ArrowRight", "Home", "End"];
      if (!keys.includes(event.key) || !root.current) return;
      event.preventDefault(); onPinChange(true);
      if (event.key === "Home") { setExpanded(false); setSize(null); try { localStorage.removeItem("siye-note-size"); } catch { /* Default size still works. */ } return; }
      if (event.key === "End" && axis !== "height") { setExpanded(true); return; }
      const rect = root.current.getBoundingClientRect();
      const width = fullscreen && axis === "height" ? size?.width ?? 360 : rect.width;
      const nextHeight = fullscreen && axis !== "height" ? size?.height ?? Math.min(760, window.innerHeight - 138) : surface.current?.getBoundingClientRect().height ?? rect.height;
      resize(width + (event.key === "ArrowLeft" ? 24 : event.key === "ArrowRight" ? -24 : 0), event.key === "End" ? window.innerHeight : nextHeight + (event.key === "ArrowDown" ? 24 : event.key === "ArrowUp" ? -24 : 0));
    }} />;
  return createPortal(<section ref={root} hidden={hidden} className={`space-edge space-edge-${side} ${open ? "is-open" : ""} ${resizing ? "is-resizing" : ""} ${fullscreen ? "is-expanded" : ""} ${splitActive ? "is-split" : ""}`} style={{ "--space-edge-height": `${height}px`, ...(splitActive ? { "--space-split-width": `${split!.noteWidth}px` } : {}), ...(side === "right" && size ? { "--space-note-width": `${size.width}px`, "--space-note-height": `${size.height}px` } : {}) } as CSSProperties}>
    <button ref={trigger} type="button" className="space-edge-trigger" aria-label={triggerLabel ?? label} aria-controls={id} aria-expanded={open} aria-hidden={open} tabIndex={open ? -1 : 0} disabled={disabled} onClick={() => onOpenChange(true)}>{icon}<span>{label}</span></button>
    <div ref={surface} id={id} className="space-edge-surface" aria-hidden={!open}>
      <div className="space-edge-head"><h2>{label}</h2><div>
        {side === "right" && !narrow && split && <button type="button" className="btn small" aria-label={t(splitActive ? "spaces.restoreLayout" : "spaces.splitPanels")} title={t(splitActive ? "spaces.restoreLayout" : "spaces.splitPanels")} aria-pressed={splitActive} onMouseDown={(event) => event.preventDefault()} onClick={splitActive ? split.restore : split.enter}>{splitActive ? <Minimize2 aria-hidden="true" /> : <Columns2 aria-hidden="true" />}</button>}
        {side === "right" && !narrow && !splitActive && <button type="button" className="btn small" aria-label={t(fullscreen ? "spaces.collapseNote" : "spaces.expandNote")} title={t(fullscreen ? "spaces.collapseNote" : "spaces.expandNote")} aria-expanded={fullscreen} onClick={() => { onPinChange(true); setExpanded(!fullscreen); }}>{fullscreen ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}</button>}
        {!splitActive && <button type="button" className="btn small" aria-label={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} aria-pressed={pinned} title={t(pinned ? "spaces.unpinPanel" : "spaces.pinPanel", { panel: label })} onClick={() => onPinChange(!pinned)}>{pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}</button>}
        <button type="button" className="btn small" aria-label={closeLabel} onClick={close}><X aria-hidden="true" /></button>
      </div></div>
      {children}
    </div>
    {side === "right" && open && !narrow && !splitActive && <>{resizeHandle("width")}{resizeHandle("height")}{resizeHandle("both")}</>}
  </section>, document.body);
}
