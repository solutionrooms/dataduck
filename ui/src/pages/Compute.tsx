// Compute: job compute node types and worker processes.

import { useSearchParams } from "react-router";
import { Cpu, Server } from "lucide-react";
import { useNodeTypes, useStatus } from "../api/hooks";
import { EmptyState, QueryState, WarningBanner } from "../components/States";
import { Tabs } from "../components/Tabs";
import { RelTime } from "../components/Time";

type ComputeTab = "job" | "workers";

export function ComputePage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as ComputeTab) || "job";
  const status = useStatus();
  const nodeTypes = useNodeTypes();
  const workers = status.data?.workers ?? [];
  const alive = workers.filter((w) => w.alive);

  return (
    <div className="panel">
      <div className="page">
        <div className="page-header">
          <h1 className="page-title">Compute</h1>
        </div>
        {status.data?.version_mismatches?.length ? (
          <WarningBanner>
            Version mismatch: {status.data.version_mismatches.join("; ")}
          </WarningBanner>
        ) : null}
        <div className="stat-row">
          <div className="stat">
            <div className="stat-label">Workers alive</div>
            <div className="stat-value">
              {status.data ? `${alive.length}/${workers.length}` : "–"}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Task slots</div>
            <div className="stat-value">{status.data ? alive.reduce((s, w) => s + w.slots, 0) : "–"}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Running tasks</div>
            <div className="stat-value">{status.data?.queue.running_tasks ?? "–"}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Pending tasks</div>
            <div className="stat-value">{status.data?.queue.pending_tasks ?? "–"}</div>
          </div>
        </div>
        <Tabs<ComputeTab>
          items={[
            { key: "job", label: "Job compute" },
            { key: "workers", label: "Workers", count: workers.length },
          ]}
          active={tab}
          onChange={(t) => setParams(t === "job" ? {} : { tab: t })}
        />
        {tab === "job" &&
          (nodeTypes.data ? (
            nodeTypes.data.length ? (
              <>
                <p className="secondary" style={{ marginTop: 0 }}>
                  Job clusters choose a node type with <span className="mono">node_type_id</span>. Each task runs in its own
                  DuckDB process with these limits.
                </p>
                <table className="dt">
                  <thead>
                    <tr>
                      <th>Node type</th>
                      <th>Description</th>
                      <th>Memory</th>
                      <th>Threads</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nodeTypes.data.map((n) => (
                      <tr key={n.node_type_id}>
                        <td>
                          <div className="name-cell">
                            <Cpu size={16} className="icon-table" />
                            <span className="mono">{n.node_type_id}</span>
                            {n.is_default && <span className="badge blue">Default</span>}
                          </div>
                        </td>
                        <td className="secondary">{n.description}</td>
                        <td className="secondary">{n.memory_limit ?? "–"}</td>
                        <td className="secondary">{n.num_cores ?? "–"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : (
              <EmptyState title="No node types" />
            )
          ) : (
            <QueryState query={nodeTypes} what="Node types" />
          ))}
        {tab === "workers" &&
          (status.data ? (
            workers.length ? (
              <table className="dt">
                <thead>
                  <tr>
                    <th>Worker</th>
                    <th>Status</th>
                    <th>Slots</th>
                    <th>DuckDB</th>
                    <th>dbt</th>
                    <th>dbt-duckdb</th>
                    <th>Last heartbeat</th>
                  </tr>
                </thead>
                <tbody>
                  {[...workers]
                    .sort((a, b) => Number(b.alive) - Number(a.alive) || (b.heartbeat_at ?? 0) - (a.heartbeat_at ?? 0))
                    .map((w) => (
                      <tr key={w.worker_id}>
                        <td>
                          <div className="name-cell">
                            <Server size={16} className="icon-folder" />
                            <span className="mono">{w.worker_id}</span>
                          </div>
                        </td>
                        <td>
                          <span className="row" style={{ gap: 6 }}>
                            <span className={`dot ${w.alive ? "green" : ""}`} />
                            {w.alive ? "Alive" : "Offline"}
                          </span>
                        </td>
                        <td className="secondary">{w.slots}</td>
                        <td className="secondary">{w.duckdb_version ?? "–"}</td>
                        <td className="secondary">
                          {w.dbt_version ?? "–"}
                          {w.dbt_distribution && <span className="badge" style={{ marginLeft: 6 }}>{w.dbt_distribution}</span>}
                        </td>
                        <td className="secondary">{w.dbt_duckdb_version ?? "–"}</td>
                        <td className="secondary">
                          <RelTime value={w.heartbeat_at} />
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            ) : (
              <EmptyState icon={<Server size={22} />} title="No workers registered" description="Start the worker service to run job tasks." />
            )
          ) : (
            <QueryState query={status} what="Platform status" />
          ))}
      </div>
    </div>
  );
}
