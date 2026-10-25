// Query history: statements from every client, with a detail drawer.

import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Search, SquareTerminal, X } from "lucide-react";
import { get } from "../api/client";
import type { QueryHistoryEntry } from "../api/types";
import { StateIcon } from "../components/Icons";
import { EmptyState, QueryState } from "../components/States";
import { DateTime, RelTime } from "../components/Time";
import { formatDuration, formatNumber } from "../lib/format";
import { addRecent } from "../lib/recents";
import type { StateInfo } from "../lib/runState";
import { openSqlTab } from "../lib/sqlTabs";

function statusInfo(q: QueryHistoryEntry): StateInfo {
  switch (q.statement_status ?? q.status) {
    case "SUCCEEDED":
    case "FINISHED":
      return { kind: "success", label: "Succeeded", active: false };
    case "FAILED":
      return { kind: "failed", label: "Failed", active: false };
    case "CANCELED":
      return { kind: "canceled", label: "Canceled", active: false };
    case "RUNNING":
      return { kind: "running", label: "Running", active: true };
    default:
      return { kind: "pending", label: "Queued", active: true };
  }
}

export function QueryHistoryPage() {
  const [limit, setLimit] = useState(100);
  const history = useQuery({
    queryKey: ["query-history", limit],
    queryFn: () =>
      get<{ res?: QueryHistoryEntry[] }>("/api/2.0/sql/history/queries", { max_results: limit }).then((r) => r.res ?? []),
    refetchInterval: (q) => (q.state.data?.some((e) => statusInfo(e).active) ? 3000 : false),
  });
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [source, setSource] = useState("all");
  const [selected, setSelected] = useState<QueryHistoryEntry | null>(null);
  const navigate = useNavigate();

  const sources = useMemo(
    () => [...new Set((history.data ?? []).map((e) => e.client_application ?? "Unknown"))].sort(),
    [history.data],
  );
  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return (history.data ?? []).filter((e) => {
      if (term && !e.query_text.toLowerCase().includes(term)) return false;
      if (status !== "all" && statusInfo(e).kind !== status) return false;
      if (source !== "all" && (e.client_application ?? "Unknown") !== source) return false;
      return true;
    });
  }, [history.data, q, status, source]);

  const openInEditor = (e: QueryHistoryEntry) => {
    openSqlTab(e.query_text, undefined, e.catalog ?? undefined, e.schema ?? undefined);
    addRecent({ kind: "query", title: e.query_text.slice(0, 60), url: "/sql/editor", subtitle: "From query history" });
    navigate("/sql/editor");
  };

  return (
    <div className="panel fixed">
      <div className="panel-scroll page">
        <div className="page-header">
          <h1 className="page-title">Query History</h1>
          <div className="page-actions">
            <button className="btn" onClick={() => history.refetch()} disabled={history.isFetching}>
              <RefreshCw size={14} className={history.isFetching ? "spin" : undefined} />
              Refresh
            </button>
          </div>
        </div>
        <div className="toolbar">
          <div className="search-input" style={{ width: 300 }}>
            <Search size={14} />
            <input className="input" placeholder="Search query text" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="select pill" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="all">Status</option>
            <option value="success">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="canceled">Canceled</option>
            <option value="running">Running</option>
          </select>
          <select className="select pill" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
            <option value="all">Source</option>
            {sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="spacer" />
          <select className="select pill" value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Show">
            {[100, 250, 500, 1000].map((n) => (
              <option key={n} value={n}>
                Last {n}
              </option>
            ))}
          </select>
        </div>
        {history.data ? (
          rows.length ? (
            <table className="dt">
              <thead>
                <tr>
                  <th>Query</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th className="num">Duration</th>
                  <th className="num">Rows</th>
                  <th>Source</th>
                  <th>Compute</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e) => {
                  const info = statusInfo(e);
                  return (
                    <tr
                      key={e.query_id}
                      className={`clickable${selected?.query_id === e.query_id ? " selected" : ""}`}
                      onClick={() => setSelected(e)}
                    >
                      <td className="mono truncate" style={{ maxWidth: 520 }} title={e.query_text}>
                        {e.query_text.replace(/\s+/g, " ")}
                      </td>
                      <td>
                        <span className={`status status-${info.kind}`} title={e.error_message ?? info.label}>
                          <StateIcon info={info} size={15} />
                          <span className="status-text">{info.label}</span>
                        </span>
                      </td>
                      <td className="secondary">
                        <RelTime value={e.query_start_time_ms} />
                      </td>
                      <td className="num secondary">{formatDuration(e.duration)}</td>
                      <td className="num secondary">{formatNumber(e.rows_produced)}</td>
                      <td className="secondary">{e.client_application ?? "–"}</td>
                      <td className="secondary">{e.warehouse_id ?? "Serverless"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <EmptyState
              compact={history.data.length > 0}
              title={history.data.length ? "No queries match the filters" : "No queries yet"}
              description={history.data.length ? undefined : "Statements run from the SQL editor, CLI, jobs and notebooks show up here."}
            />
          )
        ) : (
          <QueryState query={history} what="Query history" />
        )}
      </div>
      {selected && (
        <div className="drawer">
          <div className="drawer-header">
            <h2 className="truncate">Query details</h2>
            <button className="btn btn-sm" onClick={() => openInEditor(selected)}>
              <SquareTerminal size={13} />
              Open in SQL editor
            </button>
            <button className="icon-btn" onClick={() => setSelected(null)} aria-label="Close">
              <X size={16} />
            </button>
          </div>
          <div className="drawer-body">
            <dl className="kv">
              <dt>Status</dt>
              <dd>
                <span className={`status status-${statusInfo(selected).kind}`}>
                  <StateIcon info={statusInfo(selected)} size={15} />
                  <span className="status-text">{statusInfo(selected).label}</span>
                </span>
              </dd>
              <dt>Query ID</dt>
              <dd className="mono">{selected.query_id}</dd>
              <dt>Started</dt>
              <dd>
                <DateTime value={selected.query_start_time_ms} />
              </dd>
              <dt>Duration</dt>
              <dd>{formatDuration(selected.duration)}</dd>
              <dt>Rows produced</dt>
              <dd>{formatNumber(selected.rows_produced)}</dd>
              <dt>Source</dt>
              <dd>{selected.client_application ?? "–"}</dd>
              <dt>Compute</dt>
              <dd>{selected.warehouse_id ?? "Serverless (in-process)"}</dd>
              {(selected.catalog || selected.schema) && (
                <>
                  <dt>Context</dt>
                  <dd className="mono">{[selected.catalog, selected.schema].filter(Boolean).join(".")}</dd>
                </>
              )}
            </dl>
            {selected.error_message && (
              <>
                <div className="section-title">Error</div>
                <div className="error-block">{selected.error_message}</div>
              </>
            )}
            <div className="section-title">Query</div>
            <pre className="code-block">{selected.query_text}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
