import { CircleCheck, CircleX, Info, X } from "lucide-react";
import { dismissToast, useToasts } from "../lib/toast";

export function Toasts() {
  const toasts = useToasts();
  if (toasts.length === 0) return null;
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
          {t.kind === "error" ? <CircleX size={16} /> : t.kind === "success" ? <CircleCheck size={16} /> : <Info size={16} />}
          <div className="toast-msg">{t.message}</div>
          {t.action &&
            (t.action.href ? (
              <a className="btn btn-sm" href={t.action.href} target="_blank" rel="noreferrer">
                {t.action.label}
              </a>
            ) : (
              <button
                className="btn btn-sm"
                onClick={() => {
                  t.action?.onClick?.();
                  dismissToast(t.id);
                }}
              >
                {t.action.label}
              </button>
            ))}
          <button className="icon-btn sm" onClick={() => dismissToast(t.id)} aria-label="Dismiss">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
