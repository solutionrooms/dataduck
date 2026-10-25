import type { ReactNode } from "react";

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  count?: number;
}

export function Tabs<K extends string>({
  items,
  active,
  onChange,
  right,
}: {
  items: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  right?: ReactNode;
}) {
  return (
    <div className="tabs" role="tablist">
      {items.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={t.key === active}
          className={`tab${t.key === active ? " active" : ""}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}
          {t.count !== undefined && <span className="count">{t.count}</span>}
        </button>
      ))}
      {right && <div style={{ marginLeft: "auto", paddingBottom: 6 }}>{right}</div>}
    </div>
  );
}
