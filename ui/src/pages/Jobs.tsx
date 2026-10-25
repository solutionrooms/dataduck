// Jobs & Pipelines: jobs list, pipelines list, bundle deployments.

import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, EllipsisVertical, Layers, Package, Play, Search, SlidersHorizontal, Trash, Workflow } from "lucide-react";
import { get, post } from "../api/client";
import { keys, useHomePath, useJobs } from "../api/hooks";
import type { BundleDeployment, Job, PipelineStatus } from "../api/types";
import { ConfirmDialog } from "../components/Dialog";
import { Menu } from "../components/Menu";
import { EmptyState, QueryState } from "../components/States";
import { Tabs } from "../components/Tabs";
import { DateTime, RelTime } from "../components/Time";
import { CreateJobDialog, RecentRunSquares, RunNowDialog, TriggerCell, useRunNow } from "../components/jobs";
import { titleCase } from "../lib/runState";

type JobsTab = "jobs" | "pipelines" | "deployments";

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as JobsTab) || "jobs";
  const creating = params.get("create") === "1";
  const home = useHomePath();

  const setTab = (t: JobsTab) => setParams(t === "jobs" ? {} : { tab: t });
  const closeCreate = () => {
    const next = new URLSearchParams(params);
    next.delete("create");
    setParams(next);
  };

  return (
    <div className="panel">
      <div className="page">
        <div className="page-header">
          <h1 className="page-title">Jobs &amp; Pipelines</h1>
          <div className="page-actions">
            <Menu
              trigger={({ onClick }) => (
                <button className="btn btn-primary" onClick={onClick}>
                  Create
                  <ChevronDown size={14} />
                </button>
              )}
              items={[
                {
                  label: "Job",
                  icon: <Workflow size={16} />,
                  onClick: () => {
                    const next = new URLSearchParams(params);
                    next.set("create", "1");
                    setParams(next);
                  },
                },
                { label: "Pipeline (deploy with a bundle)", icon: <Layers size={16} />, disabled: true },
              ]}
            />
          </div>
        </div>
        <Tabs<JobsTab>
          items={[
            { key: "jobs", label: "Jobs" },
            { key: "pipelines", label: "Pipelines" },
            { key: "deployments", label: "Deployments" },
          ]}
          active={tab}
          onChange={setTab}
        />
        {tab === "jobs" && <JobsTable />}
        {tab === "pipelines" && <PipelinesTable />}
        {tab === "deployments" && <DeploymentsTable />}
      </div>
      {creating && <CreateJobDialog home={home} onClose={closeCreate} />}
    </div>
  );
}

function JobsTable() {
  const jobs = useJobs();
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(50);
  const [paramsFor, setParamsFor] = useState<Job | null>(null);
  const [deleting, setDeleting] = useState<Job | null>(null);
  const runNow = useRunNow();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const del = useMutation({
    mutationFn: (jobId: number) => post("/api/2.2/jobs/delete", { job_id: jobId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.jobs });
      setDeleting(null);
    },
  });

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    return [...(jobs.data ?? [])]
      .filter((j) => !term || (j.settings.name ?? "").toLowerCase().includes(term) || String(j.job_id) === term)
      .sort((a, b) => (a.settings.name ?? "").localeCompare(b.settings.name ?? "", undefined, { numeric: true }));
  }, [jobs.data, q]);

  return (
    <>
      <div className="toolbar">
        <div className="search-input" style={{ width: 300 }}>
          <Search size={14} />
          <input className="input" placeholder="Filter jobs by name or ID" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {jobs.data ? (
        rows.length ? (
          <>
            <table className="dt">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Trigger</th>
                  <th>Recent runs</th>
                  <th>Tasks</th>
                  <th>Created</th>
                  <th className="actions-cell" />
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((j) => (
                  <tr key={j.job_id}>
                    <td>
                      <div className="name-cell">
                        <Workflow size={16} className="icon-job" />
                        <Link to={`/jobs/${j.job_id}`} className="truncate">
                          {j.settings.name ?? `Job ${j.job_id}`}
                        </Link>
                      </div>
                    </td>
                    <td className="secondary">
                      <TriggerCell settings={j.settings} />
                    </td>
                    <td>
                      <RecentRunSquares jobId={j.job_id} />
                    </td>
                    <td className="secondary">{j.settings.tasks?.length ?? "–"}</td>
                    <td className="secondary">
                      <DateTime value={j.created_time} />
                    </td>
                    <td className="actions-cell">
                      <div className="row-actions visible">
                        <button
                          className="icon-btn"
                          title="Run now"
                          disabled={runNow.isPending}
                          onClick={() => {
                            if (j.settings.parameters?.length) setParamsFor(j);
                            else runNow.mutate({ jobId: j.job_id });
                          }}
                        >
                          <Play size={15} />
                        </button>
                        <Menu
                          trigger={({ onClick }) => (
                            <button className="icon-btn" onClick={onClick} aria-label="More actions">
                              <EllipsisVertical size={16} />
                            </button>
                          )}
                          items={[
                            { label: "Run now", icon: <Play size={16} />, onClick: () => runNow.mutate({ jobId: j.job_id }) },
                            { label: "Run now with different parameters", icon: <SlidersHorizontal size={16} />, onClick: () => setParamsFor(j) },
                            { label: "Edit JSON", icon: <Package size={16} />, onClick: () => navigate(`/jobs/${j.job_id}?tab=json`) },
                            { separator: true, label: "" },
                            { label: "Delete job", icon: <Trash size={16} />, danger: true, onClick: () => setDeleting(j) },
                          ]}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > shown && (
              <div style={{ padding: 12, textAlign: "center" }}>
                <button className="btn" onClick={() => setShown(shown + 50)}>
                  Show more ({rows.length - shown} remaining)
                </button>
              </div>
            )}
          </>
        ) : (
          <EmptyState
            icon={<Workflow size={22} />}
            title={jobs.data.length ? "No jobs match the filter" : "No jobs yet"}
            description={jobs.data.length ? undefined : "Create a job here, or deploy one with a bundle (dbk bundle deploy)."}
          />
        )
      ) : (
        <QueryState query={jobs} what="Jobs" />
      )}
      {paramsFor && <RunNowDialog job={paramsFor} onClose={() => setParamsFor(null)} />}
      {deleting && (
        <ConfirmDialog
          title="Delete job?"
          message={`“${deleting.settings.name}” and its schedule will be deleted. Run history stays in the system tables.`}
          confirmLabel="Delete"
          danger
          busy={del.isPending}
          onClose={() => setDeleting(null)}
          onConfirm={() => del.mutate(deleting.job_id)}
        />
      )}
    </>
  );
}

function stateBadge(state?: string) {
  if (!state) return <span className="muted">–</span>;
  const cls =
    state === "RUNNING" || state === "IDLE" || state === "COMPLETED"
      ? "green"
      : state === "FAILED"
        ? "red"
        : state === "CANCELED" || state === "STOPPING"
          ? "amber"
          : "blue";
  return <span className={`badge ${cls}`}>{titleCase(state)}</span>;
}

function PipelinesTable() {
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => get<{ statuses?: PipelineStatus[] }>("/api/2.0/pipelines").then((r) => r.statuses ?? []),
    refetchInterval: 10_000,
  });
  if (!pipelines.data) return <QueryState query={pipelines} what="Pipelines" />;
  if (!pipelines.data.length) {
    return (
      <EmptyState
        icon={<Layers size={22} />}
        title="No pipelines yet"
        description="Declarative pipelines are deployed with bundles (resources.pipelines)."
      />
    );
  }
  return (
    <table className="dt">
      <thead>
        <tr>
          <th>Name</th>
          <th>State</th>
          <th>Latest update</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {pipelines.data.map((p) => {
          const latest = p.latest_updates?.[0];
          return (
            <tr key={p.pipeline_id}>
              <td>
                <div className="name-cell">
                  <Layers size={16} className="icon-job" />
                  <Link to={`/pipelines/${p.pipeline_id}`}>{p.name}</Link>
                </div>
              </td>
              <td>{stateBadge(p.state)}</td>
              <td>{latest ? stateBadge(latest.state) : <span className="muted">Never run</span>}</td>
              <td className="secondary">
                <RelTime value={latest?.creation_time} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function resourceLink(r: { resource_type: string; resource_id: string; url?: string }): string | null {
  if (r.url) {
    try {
      const u = new URL(r.url, window.location.origin);
      return u.pathname + u.search;
    } catch {
      // fall through to the type-based link
    }
  }
  const t = r.resource_type.toLowerCase();
  if (t.startsWith("job")) return `/jobs/${r.resource_id}`;
  if (t.startsWith("pipeline")) return `/pipelines/${r.resource_id}`;
  return null;
}

function DeploymentsTable() {
  const bundles = useQuery({
    queryKey: ["bundles"],
    queryFn: () => get<{ deployments?: BundleDeployment[] }>("/api/duckbricks/bundles").then((r) => r.deployments ?? []),
  });
  if (!bundles.data) return <QueryState query={bundles} what="Bundle deployments" />;
  if (!bundles.data.length) {
    return (
      <EmptyState
        icon={<Package size={22} />}
        title="No bundle deployments"
        description="Run dbk bundle deploy to deploy jobs and pipelines from a databricks.yml."
      />
    );
  }
  return (
    <table className="dt">
      <thead>
        <tr>
          <th>Bundle</th>
          <th>Target</th>
          <th>Mode</th>
          <th>Resources</th>
          <th>Deployed</th>
        </tr>
      </thead>
      <tbody>
        {bundles.data.map((b) => (
          <tr key={`${b.bundle_name}/${b.target}`}>
            <td>
              <div className="name-cell">
                <Package size={16} className="icon-notebook" />
                <span>
                  <div style={{ fontWeight: 600 }}>{b.bundle_name}</div>
                  {b.root_path && <div className="muted mono" style={{ fontSize: 11 }}>{b.root_path}</div>}
                </span>
              </div>
            </td>
            <td>
              <span className="badge blue">{b.target}</span>
            </td>
            <td className="secondary">{b.mode ?? "–"}</td>
            <td>
              <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                {(b.resources ?? []).map((r) => {
                  const to = resourceLink(r);
                  const label = `${r.resource_type.replace(/s$/, "")}: ${r.resource_key}`;
                  return to ? (
                    <Link key={`${r.resource_type}/${r.resource_key}`} to={to} className="badge" style={{ color: "var(--link)" }}>
                      {label}
                    </Link>
                  ) : (
                    <span key={`${r.resource_type}/${r.resource_key}`} className="badge">
                      {label}
                    </span>
                  );
                })}
                {!b.resources?.length && <span className="muted">–</span>}
              </div>
            </td>
            <td className="secondary">
              <RelTime value={b.deployed_at} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

