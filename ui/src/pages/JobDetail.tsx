// Job detail: Runs (matrix + list), Tasks (DAG), JSON (editable settings).

import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, EllipsisVertical, Play, RotateCcw, Save, SlidersHorizontal, Trash, Workflow } from "lucide-react";
import { get, post } from "../api/client";
import { keys } from "../api/hooks";
import type { Job, JobSettings, Run, TaskSettings } from "../api/types";
import { DagGraph, type DagNode } from "../components/DagGraph";
import { ConfirmDialog } from "../components/Dialog";
import { Editor } from "../components/Editor";
import { StatusLabel, TaskTypeIcon } from "../components/Icons";
import { Menu } from "../components/Menu";
import { EmptyState, QueryState } from "../components/States";
import { Tabs } from "../components/Tabs";
import { DateTime } from "../components/Time";
import { RunNowDialog, triggerText, useJobRuns, useRunNow } from "../components/jobs";
import { formatDate, formatDuration } from "../lib/format";
import { addRecent } from "../lib/recents";
import { toast } from "../lib/toast";
import { stateInfo, taskSummary, taskType, TASK_TYPE_LABEL, triggerLabel } from "../lib/runState";

type JobTab = "runs" | "tasks" | "json";

export function useJob(jobId: number) {
  return useQuery({
    queryKey: keys.job(jobId),
    queryFn: () => get<Job>("/api/2.2/jobs/get", { job_id: jobId }),
    enabled: Number.isFinite(jobId),
  });
}

export function JobDetailPage() {
  const { jobId: raw } = useParams();
  const jobId = Number(raw);
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as JobTab) || "runs";
  const job = useJob(jobId);
  const runNow = useRunNow();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [paramsOpen, setParamsOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (job.data) addRecent({ kind: "job", title: job.data.settings.name ?? `Job ${jobId}`, url: `/jobs/${jobId}`, subtitle: `Job ${jobId}` });
  }, [job.data, jobId]);

  const del = useMutation({
    mutationFn: () => post("/api/2.2/jobs/delete", { job_id: jobId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.jobs });
      navigate("/jobs");
    },
  });

  const name = job.data?.settings.name ?? `Job ${jobId}`;
  const hasParams = !!job.data?.settings.parameters?.length;

  return (
    <div className="panel">
      <div className="page">
        <div className="breadcrumb">
          <Link to="/jobs">Jobs &amp; Pipelines</Link>
          <ChevronRight size={14} />
        </div>
        <div className="page-header">
          <h1 className="page-title">
            <Workflow size={22} className="icon-job" />
            <span className="title-text">{name}</span>
          </h1>
          {job.data && (
            <div className="page-actions">
              <div className="btn-group">
                <button
                  className="btn btn-primary"
                  disabled={runNow.isPending}
                  onClick={() =>
                    hasParams
                      ? setParamsOpen(true)
                      : runNow.mutate(
                          { jobId },
                          { onSuccess: ({ run_id }) => navigate(`/jobs/${jobId}/runs/${run_id}`) },
                        )
                  }
                >
                  <Play size={13} fill="currentColor" />
                  Run now
                </button>
                <Menu
                  trigger={({ onClick }) => (
                    <button className="btn btn-primary" onClick={onClick} aria-label="Run options" style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0, padding: "0 6px" }}>
                      <ChevronDown size={14} />
                    </button>
                  )}
                  items={[
                    { label: "Run now with different parameters", icon: <SlidersHorizontal size={16} />, onClick: () => setParamsOpen(true) },
                  ]}
                />
              </div>
              <Menu
                trigger={({ onClick }) => (
                  <button className="icon-btn" onClick={onClick} aria-label="More actions">
                    <EllipsisVertical size={16} />
                  </button>
                )}
                items={[{ label: "Delete job", icon: <Trash size={16} />, danger: true, onClick: () => setDeleting(true) }]}
              />
            </div>
          )}
        </div>

        {!job.data ? (
          <QueryState query={job} what="Jobs" />
        ) : (
          <>
            <Tabs<JobTab>
              items={[
                { key: "runs", label: "Runs" },
                { key: "tasks", label: "Tasks", count: job.data.settings.tasks?.length ?? 0 },
                { key: "json", label: "JSON" },
              ]}
              active={tab}
              onChange={(t) => setParams(t === "runs" ? {} : { tab: t })}
            />
            {tab === "json" ? (
              <JsonTab job={job.data} />
            ) : (
              <div className="home-grid" style={{ gridTemplateColumns: "minmax(0, 1fr) 280px" }}>
                <div>{tab === "runs" ? <RunsTab job={job.data} /> : <TasksTab job={job.data} />}</div>
                <JobDetailsPanel job={job.data} />
              </div>
            )}
          </>
        )}
      </div>
      {paramsOpen && job.data && <RunNowDialog job={job.data} onClose={() => setParamsOpen(false)} />}
      {deleting && (
        <ConfirmDialog
          title="Delete job?"
          message={`“${name}” and its schedule will be deleted.`}
          confirmLabel="Delete"
          danger
          busy={del.isPending}
          onClose={() => setDeleting(false)}
          onConfirm={() => del.mutate()}
        />
      )}
    </div>
  );
}

function JobDetailsPanel({ job }: { job: Job }) {
  const s = job.settings;
  const trig = triggerText(s);
  return (
    <aside className="card" style={{ padding: "14px 16px" }}>
      <div className="card-title">Job details</div>
      <dl className="kv" style={{ gridTemplateColumns: "1fr", gap: 2 }}>
        <dt className="secondary" style={{ fontSize: 12 }}>Job ID</dt>
        <dd className="mono" style={{ marginBottom: 10 }}>{job.job_id}</dd>
        <dt className="secondary" style={{ fontSize: 12 }}>Creator</dt>
        <dd style={{ marginBottom: 10 }}>{job.creator_user_name ?? "–"}</dd>
        <dt className="secondary" style={{ fontSize: 12 }}>Created</dt>
        <dd style={{ marginBottom: 10 }}>
          <DateTime value={job.created_time} />
        </dd>
        <dt className="secondary" style={{ fontSize: 12 }}>Schedules &amp; Triggers</dt>
        <dd style={{ marginBottom: 10 }}>
          {trig.text}
          {trig.detail && <div className="mono muted" style={{ fontSize: 11 }}>{trig.detail}</div>}
          {trig.paused && <span className="badge" style={{ marginTop: 4 }}>Paused</span>}
        </dd>
        <dt className="secondary" style={{ fontSize: 12 }}>Max concurrent runs</dt>
        <dd style={{ marginBottom: 10 }}>{s.max_concurrent_runs ?? 1}</dd>
        <dt className="secondary" style={{ fontSize: 12 }}>Job parameters</dt>
        <dd style={{ marginBottom: 10 }}>
          {s.parameters?.length ? (
            s.parameters.map((p) => (
              <div key={p.name} className="mono" style={{ fontSize: 12 }}>
                {p.name} <span className="muted">=</span> {p.default ?? ""}
              </div>
            ))
          ) : (
            <span className="muted">None</span>
          )}
        </dd>
        <dt className="secondary" style={{ fontSize: 12 }}>Tags</dt>
        <dd>
          {s.tags && Object.keys(s.tags).length ? (
            <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
              {Object.entries(s.tags).map(([k, v]) => (
                <span key={k} className="badge">
                  {k}
                  {v ? `: ${v}` : ""}
                </span>
              ))}
            </div>
          ) : (
            <span className="muted">None</span>
          )}
        </dd>
      </dl>
    </aside>
  );
}

// --- runs ------------------------------------------------------------------------------------

function RunMatrix({ job, runs }: { job: Job; runs: Run[] }) {
  const cols = [...runs].slice(0, 20).reverse();
  const taskKeys = useMemo(() => {
    const keysSeen = (job.settings.tasks ?? []).map((t) => t.task_key);
    for (const r of cols) for (const t of r.tasks ?? []) if (!keysSeen.includes(t.task_key)) keysSeen.push(t.task_key);
    return keysSeen;
  }, [job.settings.tasks, cols]);
  const maxDur = Math.max(1, ...cols.map((r) => r.run_duration ?? 0));
  if (!cols.length) return null;
  const template = `minmax(120px, 200px) repeat(${cols.length}, 22px)`;

  return (
    <div className="card" style={{ overflowX: "auto", marginBottom: 16 }}>
      <div className="matrix" style={{ gridTemplateColumns: template }}>
        <div className="m-label muted">Run duration</div>
        {cols.map((r) => {
          const info = stateInfo(r.state);
          const h = Math.max(4, Math.round(((r.run_duration ?? 0) / maxDur) * 68));
          return (
            <div className="m-bar-cell" key={r.run_id}>
              <Link
                to={`/jobs/${job.job_id}/runs/${r.run_id}`}
                className={`m-bar run-square k-${info.kind}`}
                style={{ height: h, width: 22, borderRadius: "3px 3px 0 0" }}
                title={`Run ${r.run_id} · ${info.label} · ${formatDuration(r.run_duration)} · ${formatDate(r.start_time)}`}
              />
            </div>
          );
        })}
        {taskKeys.map((key) => (
          <MatrixRow key={key} jobId={job.job_id} taskKey={key} runs={cols} />
        ))}
      </div>
    </div>
  );
}

function MatrixRow({ jobId, taskKey, runs }: { jobId: number; taskKey: string; runs: Run[] }) {
  return (
    <>
      <div className="m-label mono" title={taskKey}>
        {taskKey}
      </div>
      {runs.map((r) => {
        const t = r.tasks?.find((x) => x.task_key === taskKey);
        if (!t) return <span key={r.run_id} className="m-cell run-square empty" />;
        const info = stateInfo(t.state);
        return (
          <Link
            key={r.run_id}
            to={`/jobs/${jobId}/runs/${r.run_id}?task_run=${t.run_id}`}
            className={`m-cell run-square k-${info.kind}`}
            title={`${taskKey} · ${info.label} · ${formatDuration(t.execution_duration)}`}
          />
        );
      })}
    </>
  );
}

function RunsTab({ job }: { job: Job }) {
  const [limit, setLimit] = useState(25);
  const runs = useJobRuns(job.job_id, limit, true);
  if (!runs.data) return <QueryState query={runs} what="Job runs" />;
  if (!runs.data.length) {
    return <EmptyState icon={<Play size={22} />} title="No runs yet" description="Click Run now to start the first run." />;
  }
  return (
    <>
      <RunMatrix job={job} runs={runs.data} />
      <table className="dt">
        <thead>
          <tr>
            <th>Start time</th>
            <th>Run ID</th>
            <th>Launched</th>
            <th>Duration</th>
            <th>Status</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.data.map((r) => {
            const info = stateInfo(r.state);
            return (
              <tr key={r.run_id}>
                <td>
                  <Link to={`/jobs/${job.job_id}/runs/${r.run_id}`}>
                    <DateTime value={r.start_time} />
                  </Link>
                </td>
                <td className="mono">
                  <Link to={`/jobs/${job.job_id}/runs/${r.run_id}`}>{r.run_id}</Link>
                </td>
                <td className="secondary">{triggerLabel(r.trigger)}</td>
                <td className="secondary">{formatDuration(r.run_duration ?? (info.active && r.start_time ? Date.now() - r.start_time : undefined))}</td>
                <td>
                  <StatusLabel info={info} size={15} />
                </td>
                <td className="secondary truncate" style={{ maxWidth: 320 }} title={r.state.state_message}>
                  {info.kind === "failed" || info.kind === "canceled" ? r.state.state_message || "–" : "–"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {runs.data.length >= limit && limit < 100 && (
        <div style={{ padding: 12, textAlign: "center" }}>
          <button className="btn" onClick={() => setLimit(Math.min(100, limit + 25))} disabled={runs.isFetching}>
            Load more
          </button>
        </div>
      )}
    </>
  );
}

// --- tasks -----------------------------------------------------------------------------------

export function taskNodes(tasks: TaskSettings[], kindOf?: (key: string) => DagNode["kind"]): DagNode[] {
  return tasks.map((t) => {
    const type = taskType(t);
    return {
      key: t.task_key,
      dependsOn: (t.depends_on ?? []).map((d) => d.task_key),
      title: t.task_key,
      subtitle: type ? TASK_TYPE_LABEL[type] ?? type : undefined,
      kind: kindOf ? kindOf(t.task_key) : "none",
      icon: <TaskTypeIcon type={type} />,
      outcomes: Object.fromEntries((t.depends_on ?? []).filter((d) => d.outcome).map((d) => [d.task_key, d.outcome])),
    };
  });
}

function TasksTab({ job }: { job: Job }) {
  const tasks = job.settings.tasks ?? [];
  const [selected, setSelected] = useState<string | null>(tasks[0]?.task_key ?? null);
  const nodes = useMemo(() => taskNodes(tasks), [tasks]);
  const task = tasks.find((t) => t.task_key === selected);
  if (!tasks.length) return <EmptyState title="This job has no tasks" />;
  return (
    <>
      <DagGraph nodes={nodes} selected={selected} onSelect={setSelected} minHeight={240} />
      {task && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title">
            <TaskTypeIcon type={taskType(task)} />
            <span className="mono">{task.task_key}</span>
            <span className="badge">{TASK_TYPE_LABEL[taskType(task) ?? ""] ?? "Task"}</span>
          </div>
          <dl className="kv" style={{ marginBottom: 12 }}>
            <dt>Runs</dt>
            <dd className="mono">{taskSummary(task) || "–"}</dd>
            <dt>Depends on</dt>
            <dd>
              {task.depends_on?.length
                ? task.depends_on.map((d) => `${d.task_key}${d.outcome ? ` (${d.outcome})` : ""}`).join(", ")
                : "–"}
            </dd>
            {task.run_if && (
              <>
                <dt>Run if</dt>
                <dd>{task.run_if}</dd>
              </>
            )}
          </dl>
          <pre className="code-block">{JSON.stringify(task, null, 2)}</pre>
        </div>
      )}
    </>
  );
}

// --- JSON ------------------------------------------------------------------------------------

function JsonTab({ job }: { job: Job }) {
  const original = useMemo(() => JSON.stringify(job.settings, null, 2), [job.settings]);
  const [text, setText] = useState(original);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  useEffect(() => setText(original), [original]);

  const save = useMutation({
    mutationFn: (settings: JobSettings) => post("/api/2.2/jobs/reset", { job_id: job.job_id, new_settings: settings }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.job(job.job_id) });
      qc.invalidateQueries({ queryKey: keys.jobs });
      toast.success("Job settings saved");
    },
  });

  const submit = () => {
    try {
      const parsed = JSON.parse(text) as JobSettings;
      setError(null);
      save.mutate(parsed);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const dirty = text !== original;

  return (
    <>
      <div className="toolbar">
        <span className="secondary">
          Job settings as sent to <span className="mono">jobs/reset</span>. Saving replaces the whole job definition.
        </span>
        <span className="spacer" />
        <button className="btn" onClick={() => setText(original)} disabled={!dirty}>
          <RotateCcw size={13} />
          Revert
        </button>
        <button className="btn btn-primary" onClick={submit} disabled={!dirty || save.isPending}>
          <Save size={13} />
          Save
        </button>
      </div>
      {error && (
        <div className="banner error">
          <div className="banner-body">Invalid JSON: {error}</div>
        </div>
      )}
      <div style={{ height: "calc(100vh - 300px)", minHeight: 320, border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
        <Editor value={text} onChange={setText} language="json" onSave={submit} />
      </div>
    </>
  );
}
