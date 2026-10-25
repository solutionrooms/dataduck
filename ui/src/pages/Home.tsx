// Home: quick actions, recents, platform status.

import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Clock, Database, NotebookText, SquareTerminal, Workflow } from "lucide-react";
import { useHomePath, useStatus } from "../api/hooks";
import { CatalogIcon } from "../components/Icons";
import { EmptyState, ErrorBanner, Loading, WarningBanner } from "../components/States";
import { RelTime } from "../components/Time";
import { CreateObjectDialog } from "../components/WorkspaceDialogs";
import { useRecents } from "../lib/recents";
import { openSqlTab } from "../lib/sqlTabs";
import { RecentsTable } from "./Recents";

export function HomePage() {
  const status = useStatus();
  const home = useHomePath();
  const recents = useRecents();
  const navigate = useNavigate();
  const [creatingNotebook, setCreatingNotebook] = useState(false);
  const user = status.data?.user;
  const firstName = user ? user.split("@")[0].split(/[._-]/).filter((p) => p.length > 1)[0] ?? user : "";

  return (
    <div className="panel">
      <div className="page" style={{ maxWidth: 1280 }}>
        <div className="page-header" style={{ marginTop: 8 }}>
          <h1 className="page-title" style={{ fontSize: 26 }}>
            {firstName ? `Welcome to Duckbricks, ${firstName.charAt(0).toUpperCase()}${firstName.slice(1)}` : "Welcome to Duckbricks"}
          </h1>
        </div>
        <div className="quick-actions">
          <button className="quick-action" onClick={() => setCreatingNotebook(true)} disabled={!home}>
            <span className="qa-icon">
              <NotebookText size={18} />
            </span>
            <span>
              <div className="qa-title">Create a notebook</div>
              <div className="qa-sub">marimo, in your home folder</div>
            </span>
          </button>
          <button
            className="quick-action"
            onClick={() => {
              openSqlTab();
              navigate("/sql/editor");
            }}
          >
            <span className="qa-icon">
              <SquareTerminal size={18} />
            </span>
            <span>
              <div className="qa-title">Write a query</div>
              <div className="qa-sub">SQL editor</div>
            </span>
          </button>
          <Link className="quick-action" to="/jobs?create=1">
            <span className="qa-icon">
              <Workflow size={18} />
            </span>
            <span>
              <div className="qa-title">Create a job</div>
              <div className="qa-sub">Orchestrate tasks</div>
            </span>
          </Link>
          <Link className="quick-action" to="/explore/data">
            <span className="qa-icon">
              <Database size={18} />
            </span>
            <span>
              <div className="qa-title">Browse data</div>
              <div className="qa-sub">Catalog Explorer</div>
            </span>
          </Link>
        </div>

        <div className="home-grid">
          <div>
            <div className="section-title">
              <Clock size={16} /> Recents
              <span className="spacer" />
              {recents.length > 10 && (
                <Link to="/recents" style={{ fontSize: 13, fontWeight: 500 }}>
                  View all
                </Link>
              )}
            </div>
            {recents.length ? (
              <RecentsTable items={recents.slice(0, 10)} />
            ) : (
              <EmptyState compact title="Nothing opened yet" description="Things you open show up here." />
            )}
          </div>
          <div>
            <div className="section-title">Platform</div>
            {status.isPending ? (
              <Loading compact />
            ) : status.isError ? (
              <ErrorBanner error={status.error} onRetry={() => status.refetch()} />
            ) : status.data ? (
              <>
                {status.data.version_mismatches.length > 0 && (
                  <WarningBanner>{status.data.version_mismatches.join("; ")}</WarningBanner>
                )}
                <div className="card">
                  <dl className="kv">
                    <dt>Status</dt>
                    <dd>
                      <span className="row" style={{ gap: 6 }}>
                        <span className={`dot ${status.data.status === "ok" ? "green" : "amber"}`} />
                        {status.data.status === "ok" ? "Healthy" : status.data.status}
                      </span>
                    </dd>
                    <dt>Workers</dt>
                    <dd>
                      <Link to="/compute?tab=workers">
                        {status.data.workers.filter((w) => w.alive).length} alive
                      </Link>{" "}
                      <span className="muted">of {status.data.workers.length}</span>
                    </dd>
                    <dt>Queue</dt>
                    <dd>
                      {status.data.queue.running_tasks} running · {status.data.queue.pending_tasks} pending
                    </dd>
                    <dt>Warehouses</dt>
                    <dd>
                      <Link to="/sql/warehouses">
                        {status.data.warehouses.length
                          ? `${status.data.warehouses.filter((w) => w.state === "RUNNING").length} running of ${status.data.warehouses.length}`
                          : "Serverless only"}
                      </Link>
                    </dd>
                    <dt>DuckDB</dt>
                    <dd className="mono">{status.data.versions.duckdb}</dd>
                    <dt>Last worker beat</dt>
                    <dd>
                      <RelTime value={Math.max(0, ...status.data.workers.map((w) => w.heartbeat_at ?? 0)) || null} />
                    </dd>
                  </dl>
                </div>
                <div className="card">
                  <div className="card-title">Catalogs</div>
                  {status.data.catalogs.map((c) => (
                    <Link key={c.name} to={`/explore/data/${encodeURIComponent(c.name)}`} className="tree-row" style={{ paddingLeft: 4 }}>
                      <CatalogIcon />
                      <span className="label">{c.name}</span>
                      <span className="meta truncate mono">{c.data_path}</span>
                    </Link>
                  ))}
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>
      {creatingNotebook && home && <CreateObjectDialog kind="notebook" parent={home} onClose={() => setCreatingNotebook(false)} />}
    </div>
  );
}
