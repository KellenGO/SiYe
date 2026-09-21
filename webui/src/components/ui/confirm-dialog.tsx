import { useEffect, useId, useRef } from "react";

/**
 * 屏幕正中的确认框。
 *
 * 项目里原来所有删除确认都用 `window.confirm`（浏览器原生弹窗，位置由浏览器
 * 决定、样式跟界面不一致），破坏性操作统一改到这里，确认按钮可标红。
 */
export function ConfirmDialog({
  open, title, description, hint, confirmLabel = "确定", danger = false, busy = false,
  rememberLabel, rememberChecked = false, onRememberChange, onConfirm, onCancel,
}: {
  open: boolean;
  title: string;
  description?: string;
  /** 补充说明，用更小的字号显示在正文下面。 */
  hint?: string;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  /** 传了就显示"下次不再提示"复选框，勾选状态由调用方持有。 */
  rememberLabel?: string;
  rememberChecked?: boolean;
  onRememberChange?: (checked: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    // 焦点默认给「取消」：破坏性操作不该让回车直接生效
    cancel.current?.focus();
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
        {hint && <p className="confirm-hint">{hint}</p>}
        {rememberLabel && <label className="confirm-remember">
          <input type="checkbox" checked={rememberChecked} onChange={(event) => onRememberChange?.(event.target.checked)} />
          <span>{rememberLabel}</span>
        </label>}
        <div className="confirm-actions">
          <button type="button" className="btn" ref={cancel} onClick={onCancel}>取消</button>
          <button type="button" className={danger ? "btn danger-solid" : "btn primary"} disabled={busy} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
