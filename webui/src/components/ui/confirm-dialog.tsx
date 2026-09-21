import { useEffect, useId, useRef } from "react";

/**
 * 屏幕正中的确认框。
 *
 * 项目里原来所有删除确认都用 `window.confirm`（浏览器原生弹窗，位置由浏览器
 * 决定、样式跟界面不一致），破坏性操作统一改到这里，确认按钮可标红。
 */
export function ConfirmDialog({ open, title, description, confirmLabel = "确定", danger = false, busy = false, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    confirm.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onCancel(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="confirm-overlay" onPointerDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
      <div className="confirm-card" role="dialog" aria-modal="true" aria-labelledby={id}>
        <h2 id={id} className="confirm-title">{title}</h2>
        {description && <p className="confirm-text">{description}</p>}
        <div className="confirm-actions">
          <button type="button" className="btn" onClick={onCancel}>取消</button>
          <button type="button" ref={confirm} className={danger ? "btn danger" : "btn primary"} disabled={busy} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
