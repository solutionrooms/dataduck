// Recently opened objects (kept in this browser).

import { Link } from "react-router";
import { Clock } from "lucide-react";
import { RecentIcon } from "../components/Icons";
import { EmptyState } from "../components/States";
import { RelTime } from "../components/Time";
import { clearRecents, recentKindLabel, useRecents, type RecentItem } from "../lib/recents";

export function RecentLink({ item }: { item: RecentItem }) {
  if (item.href) {
    return (
      <a href={item.href} target="_blank" rel="noreferrer">
        {item.title}
      </a>
    );
  }
  return <Link to={item.url}>{item.title}</Link>;
}

export function RecentsTable({ items }: { items: RecentItem[] }) {
  return (
    <table className="dt">
      <thead>
        <tr>
          <th>Name</th>
          <th>Type</th>
          <th>Location</th>
          <th>Opened</th>
        </tr>
      </thead>
      <tbody>
        {items.map((r) => (
          <tr key={`${r.kind}:${r.url}`}>
            <td>
              <div className="name-cell">
                <RecentIcon kind={r.kind} />
                <span className="truncate">
                  <RecentLink item={r} />
                </span>
              </div>
            </td>
            <td className="secondary">{recentKindLabel[r.kind]}</td>
            <td className="secondary truncate" style={{ maxWidth: 360 }}>
              {r.subtitle ?? "–"}
            </td>
            <td className="secondary">
              <RelTime value={r.openedAt} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function RecentsPage() {
  const recents = useRecents();
  return (
    <div className="panel">
      <div className="page">
        <div className="page-header">
          <h1 className="page-title">Recents</h1>
          <div className="page-actions">
            <button className="btn" onClick={clearRecents} disabled={!recents.length}>
              Clear
            </button>
          </div>
        </div>
        {recents.length ? (
          <RecentsTable items={recents} />
        ) : (
          <EmptyState
            icon={<Clock size={22} />}
            title="Nothing opened yet"
            description="Files, notebooks, tables, jobs, runs and queries you open appear here."
          />
        )}
      </div>
    </div>
  );
}
