import { useEffect, useRef, type FormEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { Spinner } from "./States";

export function Dialog({
  title,
  onClose,
  children,
  footer,
  wide,
  onSubmit,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  onSubmit?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const first =
      ref.current?.querySelector<HTMLElement>("input, textarea, select") ??
      ref.current?.querySelector<HTMLElement>('button[type="submit"]');
    first?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const content = (
    <>
      <div className="dialog-header">
        <h2>{title}</h2>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="dialog-body">{children}</div>
      {footer && <div className="dialog-footer">{footer}</div>}
    </>
  );

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dialog${wide ? " wide" : ""}`} ref={ref} role="dialog" aria-modal="true">
        {onSubmit ? (
          <form
            style={{ display: "contents" }}
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              onSubmit();
            }}
          >
            {content}
          </form>
        ) : (
          content
        )}
      </div>
    </div>
  );
}

/** Standard Cancel / Confirm footer. */
export function DialogButtons({
  onCancel,
  confirmLabel,
  busy,
  disabled,
  danger,
}: {
  onCancel: () => void;
  confirmLabel: string;
  busy?: boolean;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <>
      <button type="button" className="btn" onClick={onCancel}>
        Cancel
      </button>
      <button type="submit" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={busy || disabled}>
        {busy && <Spinner size={14} />}
        {confirmLabel}
      </button>
    </>
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger,
  busy,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Dialog
      title={title}
      onClose={onClose}
      onSubmit={onConfirm}
      footer={<DialogButtons onCancel={onClose} confirmLabel={confirmLabel} danger={danger} busy={busy} />}
    >
      <div className="secondary">{message}</div>
    </Dialog>
  );
}
