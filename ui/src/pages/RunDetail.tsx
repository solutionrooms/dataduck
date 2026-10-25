// Run detail: task DAG coloured by state, run details, task side panel with logs.

import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, FileCode, LoaderCircle, Play, RefreshCw, Square, X } from "lucide-react";
import { get, post } from "../api/client";
import { keys } from "../api/hooks";
import type { Run, RunOutput, TaskRun } from "../api/types";
import { DagGraph } from "../components/DagGraph";
import { StatusLabel, TaskTypeIcon } from "../components/Icons";
import { EmptyState, QueryState, Spinner } from "../components/States";
import { DateTime, RelTime } from "../components/Time";
import { useRunNow } from "../components/jobs";
import { formatDuration } from "../lib/format";
import { editorUrl } from "../lib/paths";
import { addRecent } from "../lib/recents";
import { isActive, stateInfo, taskSummary, taskType, TASK_TYPE_LABEL, triggerLabel } from "../lib/runState";
import { taskNodes } from "./JobDetail";

export function RunDetailPage() {
  const { runId: raw, jobId: rawJob } = useParams();
  const runId = Number(raw);
  const [params, setParams] = useSearchParams();
  const selectedTaskRun = params.get("task_run");
  const qc = useQueryClient();
  const navigate = useNavigate();
  const runNow = useRunNow();

  const run = useQuery({
    queryKey: keys.run(runId),
    queryFn: () => get<Run>("/api/2.2/jobs/runs/get", { run_id: runId }),
    refetchInterval: (q) => (isActive(q.state.data?.state) ? 2000 : false),
  });
  const r = run.data;
  const jobId = r?.job_id ?? (rawJob ? Number(rawJob) : undefined);
  const info = stateInfo(r?.state);
  const active = !!r && isActive(r.state);

  useEffect(() => {
    if (r) addRecent({ kind: "run", title: `${r.run_name ?? "Run"} #${r.run_id}`, url: `/jobs/${r.job_id}/runs/${r.run_id}`, subtitle: `Job ${r.job_id}` });
  }, [r?.run_id, r?.job_id, r?.run_name]); // only when the run identity changes, not on every poll

  const cancel = useMutation({
    mutationFn: () => post("/api/2.2/jobs/runs/cancel", { run_id: runId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.run(runId) }),
  });

  const tasks = r?.tasks ?? [];
  const byKey = useMemo(() => new Map(tasks.map((t) => [t.task_key, t])), [tasks]);
  const nodes = useMemo(
    () =>
      taskNodes(tasks, (key) => stateInfo(byKey.get(key)?.state).kind).map((n) => {
        const t = byKey.get(n.key);
        const ti = stateInfo(t?.state);
        const dur = t?.execution_duration || (ti.active && t?.start_time ? Date.now() - t.start_time : undefined);
        return {
          ...n,
          subtitle: `${ti.label}${dur ? ` · ${formatDuration(dur)}` : ""}`,
          icon: ti.kind === "running" ? <LoaderCircle size={16} className="spin" style={{ color: "var(--running)" }} /> : n.icon,
        };
      }),
    [tasks, byKey],
  );
  const selectedTask = tasks.find((t) => String(t.run_id) === selectedTaskRun) ?? null;
  const selectTask = (key: string | null) => {
    const t = key ? byKey.get(key) : null;
    setParams(t ? { task_run: String(t.run_id) } : {}, { replace: true });
  };

  const duration = r?.run_duration || (active && r?.start_time ? Date.now() - r.start_time : undefined);

  return (
    <div className="panel fixed">
      <div className="panel-scroll page">
        <div className="breadcrumb">
          <Link to="/jobs">Jobs &amp; Pipelines</Link>
          <ChevronRight size={14} />
          {jobId !== undefined && (
            <>
              <Link to={`/jobs/${jobId}`}>{r?.run_name ?? `Job ${jobId}`}</Link>
              <ChevronRight size={14} />
            </>
          )}
        </div>
        <div className="page-header">
          <h1 className="page-title">
            <span className="title-text">Run {runId}</span>
            {r && <StatusLabel info={info} size={18} />}
          </h1>
          <div className="page-actions">
            {active && (
              <button className="btn" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                <Square size={12} fill="currentColor" />
                Cancel run
              </button>
            )}
            {r && !active && (
              <button
                className="btn"
                disabled={runNow.isPending}
                onClick={() =>
                  runNow.mutate(
                    {
                      jobId: r.job_id,
                      params: Object.fromEntries((r.job_parameters ?? []).map((p) => [p.name, p.value ?? p.default ?? ""])),
                    },
                    { onSuccess: ({ run_id }) => navigate(`/jobs/${r.job_id}/runs/${run_id}`) },
                  )
                }
              >
                <Play size={13} />
                Run again
              </button>
            )}
            <button className="icon-btn" title="Refresh" onClick={() => run.refetch()}>
              <RefreshCw size={15} className={run.isFetching ? "spin" : undefined} />
            </button>
          </div>
        </div>

        {!r ? (
          <QueryState query={run} what="Job runs" />
        ) : (
          <div className="home-grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 280px" }}>
            <div>
              {r.state.state_message && (info.kind === "failed" || info.kind === "canceled") && (
                <div className={`banner ${info.kind === "failed" ? "error" : "warning"}`}>
                  <div className="banner-body">{r.state.state_message}</div>
                </div>
              )}
              {tasks.length ? (
                <DagGraph nodes={nodes} selected={selectedTask?.task_key} onSelect={selectTask} minHeight={260} />
              ) : (
                <EmptyState compact title="No tasks in this run" />
              )}
              <div className="section-title">Tasks</div>
              <table className="dt compact">
                <thead>
                  <tr>
                    <th>Task</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Duration</th>
                    <th>Attempt</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((t) => (
                    <tr
                      key={t.run_id}
                      className={`clickable${selectedTask?.run_id === t.run_id ? " selected" : ""}`}
                      onClick={() => selectTask(t.task_key)}
                    >
                      <td>
                        <div className="name-cell">
                          <TaskTypeIcon type={taskType(t)} size={15} />
                          <span className="mono">{t.task_key}</span>
                        </div>
                      </td>
                      <td className="secondary">{TASK_TYPE_LABEL[taskType(t) ?? ""] ?? "–"}</td>
                      <td>
                        <StatusLabel info={stateInfo(t.state)} size={14} />
                      </td>
                      <td className="secondary">
                        <DateTime value={t.start_time} />
                      </td>
                      <td className="secondary">
                        {formatDuration(t.execution_duration || (isActive(t.state) && t.start_time ? Date.now() - t.start_time : undefined))}
                      </td>
                      <td className="secondary">{(t.attempt_number ?? 0) + 1}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <aside className="card" style={{ padding: "14px 16px" }}>
              <div className="card-title">Run details</div>
              <dl className="kv" style={{ gridTemplateColumns: "1fr", gap: 2 }}>
                <Detail label="Job">
                  <Link to={`/jobs/${r.job_id}`}>{r.run_name ?? `Job ${r.job_id}`}</Link>
                </Detail>
                <Detail label="Job run ID">
                  <span className="mono">{r.run_id}</span>
                </Detail>
                <Detail label="Status">
                  <StatusLabel info={info} size={14} />
                </Detail>
                <Detail label="Started">
                  <DateTime value={r.start_time} />
                </Detail>
                <Detail label="Ended">
                  {r.end_time ? <DateTime value={r.end_time} /> : active ? "Running" : "–"}
                </Detail>
                <Detail label="Duration">{formatDuration(duration)}</Detail>
                <Detail label="Launched">{triggerLabel(r.trigger)}</Detail>
                <Detail label="Run type">{r.run_type ?? "–"}</Detail>
                {r.creator_user_name && <Detail label="Run as">{r.creator_user_name}</Detail>}
                <Detail label="Job parameters">
                  {r.job_parameters?.length ? (
                    r.job_parameters.map((p) => (
                      <div key={p.name} className="mono" style={{ fontSize: 12 }}>
                        {p.name} <span className="muted">=</span> {p.value ?? p.default ?? ""}
                      </div>
                    ))
                  ) : (
                    <span className="muted">None</span>
                  )}
                </Detail>
              </dl>
            </aside>
          </div>
        )}
      </div>
      {selectedTask && <TaskPanel task={selectedTask} onClose={() => selectTask(null)} />}
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="secondary" style={{ fontSize: 12 }}>
        {label}
      </dt>
      <dd style={{ marginBottom: 10 }}>{children}</dd>
    </>
  );
}

function workspaceFileOf(task: TaskRun): string | null {
  const t = taskType(task);
  const cfg = (t ? task[t] : null) as Record<string, any> | null;
  if (!cfg) return null;
  const p = cfg.python_file ?? cfg.file?.path ?? cfg.notebook_path ?? null;
  return typeof p === "string" && p.startsWith("/") ? p : null;
}

function TaskPanel({ task, onClose }: { task: TaskRun; onClose: () => void }) {
  const info = stateInfo(task.state);
  const active = isActive(task.state);
  const started = !!task.start_time;
  const output = useQuery({
    queryKey: ["run-output", task.run_id],
    queryFn: () => get<RunOutput>("/api/2.2/jobs/runs/get-output", { run_id: task.run_id }),
    refetchInterval: active ? 2000 : false,
    enabled: started || !active,
    retry: false,
  });
  const logs = useQuery({
    queryKey: ["run-logs", task.run_id],
    queryFn: () => get<{ logs?: string; truncated?: boolean }>(`/api/duckbricks/runs/${task.run_id}/logs`),
    refetchInterval: active ? 2000 : false,
    enabled: started || !active,
    retry: false,
  });
  // When the task finishes, fetch once more so the final log lines and error arrive.
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) {
      logs.refetch();
      output.refetch();
    }
    wasActive.current = active;
  }, [active, logs, output]);
  const logRef = useRef<HTMLPreElement>(null);
  const logText = logs.data?.logs ?? output.data?.logs ?? "";
  useEffect(() => {
    const el = logRef.current;
    if (el && active) el.scrollTop = el.scrollHeight;
  }, [logText, active]);

  const file = workspaceFileOf(task);
  const error = output.data?.error;
  const trace = output.data?.error_trace;
  const duration = task.execution_duration || (active && task.start_time ? Date.now() - task.start_time : undefined);

  return (
    <div className="drawer" role="dialog" aria-label={`Task ${task.task_key}`}>
      <div className="drawer-header">
        <TaskTypeIcon type={taskType(task)} />
        <h2 className="truncate mono">{task.task_key}</h2>
        <StatusLabel info={info} size={15} />
        <button className="icon-btn" onClick={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </div>
      <div className="drawer-body">
        <dl className="kv">
          <dt>Task run ID</dt>
          <dd className="mono">{task.run_id}</dd>
          <dt>Type</dt>
          <dd>{TASK_TYPE_LABEL[taskType(task) ?? ""] ?? "–"}</dd>
          <dt>Runs</dt>
          <dd className="mono">
            {file ? (
              <Link to={editorUrl(file)}>
                <FileCode size={12} style={{ verticalAlign: -1, marginRight: 4 }} />
                {file}
              </Link>
            ) : (
              taskSummary(task) || "–"
            )}
          </dd>
          <dt>Started</dt>
          <dd>{task.start_time ? <RelTime value={task.start_time} /> : "–"}</dd>
          <dt>Duration</dt>
          <dd>{formatDuration(duration)}</dd>
          <dt>Attempt</dt>
          <dd>{(task.attempt_number ?? 0) + 1}</dd>
          {task.depends_on?.length ? (
            <>
              <dt>Depends on</dt>
              <dd className="mono">{task.depends_on.map((d) => d.task_key).join(", ")}</dd>
            </>
          ) : null}
          {task.state.state_message && (
            <>
              <dt>Message</dt>
              <dd>{task.state.state_message}</dd>
            </>
          )}
        </dl>

        {error && (
          <>
            <div className="section-title">Error</div>
            <div className="error-block">{error}</div>
          </>
        )}
        {trace && trace !== error && (
          <>
            <div className="section-title">Error trace</div>
            <pre className="log-view" style={{ maxHeight: 280 }}>{trace}</pre>
          </>
        )}

        <div className="section-title">
          Logs
          {active && <Spinner size={13} />}
          {logs.data?.truncated && <span className="badge amber">Tail only</span>}
          <span className="spacer" />
          <button className="btn btn-sm btn-ghost" onClick={() => { logs.refetch(); output.refetch(); }}>
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
        {!started && active ? (
          <div className="muted">The task has not started yet.</div>
        ) : logs.isPending && output.isPending ? (
          <div className="loading-inline">
            <Spinner /> Loading logs…
          </div>
        ) : logText ? (
          <pre className="log-view" ref={logRef}>{logText}</pre>
        ) : logs.isError && output.isError ? (
          <QueryState query={logs} what="Task logs" compact />
        ) : (
          <div className="muted">No log output.</div>
        )}
      </div>
    </div>
  );
}
