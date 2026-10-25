// Job runs across every job, filterable by state and job.

import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Play, RefreshCw } from "lucide-react";
import { get } from "../api/client";
import { useJobs } from "../api/hooks";
import type { Run } from "../api/types";
import { StatusLabel } from "../components/Icons";
import { EmptyState, QueryState } from "../components/States";
import { DateTime } from "../components/Time";
import { formatDuration } from "../lib/format";
import { isActive, stateInfo, triggerLabel, type StateKind } from "../lib/runState";

const FILTERS: { key: string; label: string; kinds?: StateKind[] }[] = [
  { key: "all", label: "All states" },
  { key: "active", label: "Active", kinds: ["running", "pending", "queued", "blocked"] },
  { key: "success", label: "Succeeded", kinds: ["success"] },
  { key: "failed", label: "Failed", kinds: ["failed", "upstream"] },
  { key: "canceled", label: "Canceled", kinds: ["canceled"] },
  { key: "skipped", label: "Skipped", kinds: ["skipped", "excluded"] },
];

export function RunsPage() {
  const [state, setState] = useState("all");
  const [jobId, setJobId] = useState("");
  const jobs = useJobs();
  const runs = useQuery({
    queryKey: ["all-runs", jobId],
    queryFn: () =>
      get<{ runs?: Run[] }>("/api/2.2/jobs/runs/list", { limit: 100, job_id: jobId || undefined }).then((r) => r.runs ?? []),
    refetchInterval: (q) => (q.state.data?.some((r) => isActive(r.state)) ? 3000 : 15_000),
  });

  const jobNames = useMemo(() => new Map((jobs.data ?? []).map((j) => [j.job_id, j.settings.name ?? `Job ${j.job_id}`])), [jobs.data]);
  const rows = useMemo(() => {
    const kinds = FILTERS.find((f) => f.key === state)?.kinds;
    return (runs.data ?? []).filter((r) => !kinds || kinds.includes(stateInfo(r.state).kind));
  }, [runs.data, state]);

  return (
    <div className="panel">
      <div className="page">
        <div className="page-header">
          <h1 className="page-title">Job runs</h1>
          <div className="page-actions">
            <button className="btn" onClick={() => runs.refetch()} disabled={runs.isFetching}>
              <RefreshCw size={14} className={runs.isFetching ? "spin" : undefined} />
              Refresh
            </button>
          </div>
        </div>
        <div className="toolbar">
          <select className="select pill" value={state} onChange={(e) => setState(e.target.value)} aria-label="Run state">
            {FILTERS.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
          <select className="select pill" value={jobId} onChange={(e) => setJobId(e.target.value)} aria-label="Job" style={{ maxWidth: 320 }}>
            <option value="">All jobs</option>
            {[...(jobs.data ?? [])]
              .sort((a, b) => (a.settings.name ?? "").localeCompare(b.settings.name ?? ""))
              .map((j) => (
                <option key={j.job_id} value={j.job_id}>
                  {j.settings.name ?? `Job ${j.job_id}`}
                </option>
              ))}
          </select>
          <span className="muted">Showing the latest 100 runs</span>
        </div>
        {runs.data ? (
          rows.length ? (
            <table className="dt">
              <thead>
                <tr>
                  <th>Start time</th>
                  <th>Job</th>
                  <th>Run ID</th>
                  <th>Launched</th>
                  <th>Duration</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const info = stateInfo(r.state);
                  const url = `/jobs/${r.job_id}/runs/${r.run_id}`;
                  return (
                    <tr key={r.run_id}>
                      <td>
                        <Link to={url}>
                          <DateTime value={r.start_time} />
                        </Link>
                      </td>
                      <td>
                        <Link to={`/jobs/${r.job_id}`}>{jobNames.get(r.job_id) ?? r.run_name ?? `Job ${r.job_id}`}</Link>
                      </td>
                      <td className="mono">
                        <Link to={url}>{r.run_id}</Link>
                      </td>
                      <td className="secondary">{triggerLabel(r.trigger)}</td>
                      <td className="secondary">
                        {formatDuration(r.run_duration || (info.active && r.start_time ? Date.now() - r.start_time : undefined))}
                      </td>
                      <td title={r.state.state_message}>
                        <StatusLabel info={info} size={15} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <EmptyState
              icon={<Play size={22} />}
              compact={(runs.data ?? []).length > 0}
              title={runs.data.length ? "No runs match the filters" : "No job runs yet"}
            />
          )
        ) : (
          <QueryState query={runs} what="Job runs" />
        )}
      </div>
    </div>
  );
}
