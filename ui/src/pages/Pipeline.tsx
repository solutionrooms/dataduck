// Pipeline detail: start / full refresh, latest updates, event log.

import { Fragment, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Layers, Play, RefreshCw, RotateCcw } from "lucide-react";
import { get, post } from "../api/client";
import type { PipelineEvent, PipelineStatus } from "../api/types";
import { EmptyState, QueryState } from "../components/States";
import { DateTime, RelTime } from "../components/Time";
import { addRecent } from "../lib/recents";
import { titleCase } from "../lib/runState";
import { toast } from "../lib/toast";

const ACTIVE = new Set(["QUEUED", "CREATED", "WAITING_FOR_RESOURCES", "INITIALIZING", "RESETTING", "SETTING_UP_TABLES", "RUNNING", "STOPPING"]);

function stateBadge(state?: string) {
  if (!state) return <span className="muted">–</span>;
  const cls = state === "COMPLETED" || state === "IDLE" ? "green" : state === "FAILED" ? "red" : state === "CANCELED" ? "amber" : "blue";
  return <span className={`badge ${cls}`}>{titleCase(state)}</span>;
}

function levelBadge(level?: string) {
  const cls = level === "ERROR" ? "red" : level === "WARN" ? "amber" : level === "METRICS" ? "blue" : "";
  return <span className={`badge ${cls}`}>{level ?? "INFO"}</span>;
}

export function PipelinePage() {
  const { pipelineId = "" } = useParams();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);

  const pipeline = useQuery({
    queryKey: ["pipeline", pipelineId],
    queryFn: () => get<PipelineStatus>(`/api/2.0/pipelines/${encodeURIComponent(pipelineId)}`),
    refetchInterval: (q) => (q.state.data?.latest_updates?.some((u) => ACTIVE.has(u.state)) ? 3000 : 15_000),
  });
  const events = useQuery({
    queryKey: ["pipeline-events", pipelineId],
    queryFn: () =>
      get<{ events?: PipelineEvent[] }>(`/api/2.0/pipelines/${encodeURIComponent(pipelineId)}/events`).then((r) => r.events ?? []),
    refetchInterval: pipeline.data?.latest_updates?.some((u) => ACTIVE.has(u.state)) ? 3000 : false,
  });

  useEffect(() => {
    if (pipeline.data) addRecent({ kind: "pipeline", title: pipeline.data.name, url: `/pipelines/${pipelineId}`, subtitle: "Pipeline" });
  }, [pipeline.data?.name, pipelineId]); // only when the pipeline identity changes

  const start = useMutation({
    mutationFn: (fullRefresh: boolean) =>
      post<{ update_id: string }>(`/api/2.0/pipelines/${encodeURIComponent(pipelineId)}/updates`, { full_refresh: fullRefresh }),
    onSuccess: (r, full) => {
      toast.success(`${full ? "Full refresh" : "Update"} started (${r.update_id})`);
      qc.invalidateQueries({ queryKey: ["pipeline", pipelineId] });
      qc.invalidateQueries({ queryKey: ["pipeline-events", pipelineId] });
    },
  });

  const p = pipeline.data;
  const running = !!p?.latest_updates?.some((u) => ACTIVE.has(u.state));

  return (
    <div className="panel">
      <div className="page">
        <div className="breadcrumb">
          <Link to="/jobs?tab=pipelines">Jobs &amp; Pipelines</Link>
          <ChevronRight size={14} />
        </div>
        <div className="page-header">
          <h1 className="page-title">
            <Layers size={22} className="icon-job" />
            <span className="title-text">{p?.name ?? pipelineId}</span>
            {p?.state && stateBadge(p.state)}
          </h1>
          {p && (
            <div className="page-actions">
              <button className="btn" onClick={() => start.mutate(true)} disabled={start.isPending || running}>
                <RotateCcw size={13} />
                Full refresh
              </button>
              <button className="btn btn-primary" onClick={() => start.mutate(false)} disabled={start.isPending || running}>
                <Play size={13} fill="currentColor" />
                Start
              </button>
            </div>
          )}
        </div>
        {!p ? (
          <QueryState query={pipeline} what="Pipelines" />
        ) : (
          <>
            <div className="section-title">Latest updates</div>
            {p.latest_updates?.length ? (
              <table className="dt compact">
                <thead>
                  <tr>
                    <th>Update</th>
                    <th>State</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {p.latest_updates.map((u) => (
                    <tr key={u.update_id}>
                      <td className="mono">{u.update_id}</td>
                      <td>{stateBadge(u.state)}</td>
                      <td className="secondary">
                        <DateTime value={u.creation_time} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="muted">No updates yet. Click Start to run the pipeline.</div>
            )}

            <div className="section-title">
              Event log
              <span className="spacer" />
              <button className="btn btn-sm btn-ghost" onClick={() => events.refetch()}>
                <RefreshCw size={12} className={events.isFetching ? "spin" : undefined} />
                Refresh
              </button>
            </div>
            {events.data ? (
              events.data.length ? (
                <table className="dt compact">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Level</th>
                      <th>Type</th>
                      <th>Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.data.map((e) => (
                      <Fragment key={e.id}>
                        <tr className="clickable" onClick={() => setExpanded(expanded === e.id ? null : e.id)}>
                          <td className="secondary">
                            <RelTime value={e.timestamp} />
                          </td>
                          <td>{levelBadge(e.level)}</td>
                          <td className="mono secondary">{e.event_type ?? "–"}</td>
                          <td>{e.message ?? ""}</td>
                        </tr>
                        {expanded === e.id && e.details !== undefined && (
                          <tr>
                            <td colSpan={4}>
                              <pre className="code-block">{JSON.stringify(e.details, null, 2)}</pre>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              ) : (
                <EmptyState compact title="No events yet" />
              )
            ) : (
              <QueryState query={events} what="Pipeline events" compact />
            )}
          </>
        )}
      </div>
    </div>
  );
}
