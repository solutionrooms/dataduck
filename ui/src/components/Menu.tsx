import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";

export interface MenuItem {
  label: string;
  icon?: ReactNode;
  onClick?: () => void;
  to?: string;
  href?: string;
  danger?: boolean;
  disabled?: boolean;
  separator?: boolean;
  heading?: boolean;
}

/** A trigger that opens a dropdown menu; closes on outside click, Escape or selection. */
export function Menu({
  trigger,
  items,
  align = "right",
}: {
  trigger: (props: { onClick: () => void; open: boolean }) => ReactNode;
  items: MenuItem[];
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="menu-anchor" ref={ref} onClick={(e) => e.stopPropagation()}>
      {trigger({ onClick: () => setOpen((o) => !o), open })}
      {open && (
        <div className={`menu align-${align}`} role="menu">
          {items.map((item, i) => {
            if (item.separator) return <div key={i} className="menu-sep" />;
            if (item.heading) {
              return (
                <div key={i} className="menu-label">
                  {item.label}
                </div>
              );
            }
            const cls = `menu-item${item.danger ? " danger" : ""}`;
            const close = () => setOpen(false);
            if (item.to && !item.disabled) {
              return (
                <Link key={i} to={item.to} className={cls} onClick={close} role="menuitem">
                  {item.icon}
                  {item.label}
                </Link>
              );
            }
            if (item.href && !item.disabled) {
              return (
                <a key={i} href={item.href} target="_blank" rel="noreferrer" className={cls} onClick={close}>
                  {item.icon}
                  {item.label}
                </a>
              );
            }
            return (
              <button
                key={i}
                type="button"
                className={cls}
                disabled={item.disabled}
                role="menuitem"
                onClick={() => {
                  close();
                  item.onClick?.();
                }}
              >
                {item.icon}
                {item.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
