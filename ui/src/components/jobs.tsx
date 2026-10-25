// Shared pieces for Jobs pages: trigger text, recent-run squares, run-now dialog, create dialog.

import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../api/client";
import { keys } from "../api/hooks";
import type { Job, JobSettings, Run } from "../api/types";
import { describeQuartz, formatDate, formatDuration } from "../lib/format";
import { isActive, stateInfo, taskType, TASK_TYPE_LABEL } from "../lib/runState";
import { toast } from "../lib/toast";
import { Dialog, DialogButtons } from "./Dialog";
import { Editor } from "./Editor";

export function triggerText(settings: JobSettings): { text: string; detail?: string; paused: boolean } {
  if (settings.schedule?.quartz_cron_expression) {
    const s = settings.schedule;
    return {
      text: describeQuartz(s.quartz_cron_expression),
      detail: `${s.quartz_cron_expression} (${s.timezone_id ?? "UTC"})`,
      paused: s.pause_status === "PAUSED",
    };
  }
  if (settings.trigger) {
    const t = settings.trigger;
    if (t.periodic) {
      return { text: `Every ${t.periodic.interval} ${t.periodic.unit.toLowerCase()}`, paused: t.pause_status === "PAUSED" };
    }
    const kind = Object.keys(t).find((k) => k !== "pause_status");
    return { text: kind ? kind.replace(/_/g, " ") : "Trigger", paused: t.pause_status === "PAUSED" };
  }
  return { text: "None", paused: false };
}

export function TriggerCell({ settings }: { settings: JobSettings }) {
  const t = triggerText(settings);
  if (t.text === "None") return <span className="muted">None</span>;
  return (
    <span className="row" style={{ gap: 6 }} title={t.detail}>
      <span>{t.text}</span>
      {t.detail && <span className="muted" style={{ fontSize: 12 }}>{t.detail.match(/\((.*)\)$/)?.[1]}</span>}
      {t.paused && <span className="badge">Paused</span>}
    </span>
  );
}

export function useJobRuns(jobId: number, limit = 25, expand = false) {
  return useQuery({
    queryKey: [...keys.jobRuns(jobId, limit), expand],
    queryFn: () =>
      get<{ runs?: Run[]; has_more?: boolean }>("/api/2.2/jobs/runs/list", {
        job_id: jobId,
        limit,
        expand_tasks: expand,
      }).then((r) => r.runs ?? []),
    refetchInterval: (q) => (q.state.data?.some((r) => isActive(r.state)) ? 3000 : 20_000),
  });
}

/** Last runs as coloured squares, oldest on the left. */
export function RecentRunSquares({ jobId, count = 10 }: { jobId: number; count?: number }) {
  const runs = useJobRuns(jobId, count);
  if (runs.isPending) return <span className="muted">…</span>;
  if (runs.isError) return <span className="muted">–</span>;
  const list = [...(runs.data ?? [])].reverse();
  if (!list.length) return <span className="muted">No runs yet</span>;
  return (
    <span className="run-squares">
      {list.map((r) => {
        const info = stateInfo(r.state);
        return (
          <Link
            key={r.run_id}
            to={`/jobs/${jobId}/runs/${r.run_id}`}
            className={`run-square k-${info.kind}`}
            title={`${info.label} · ${formatDate(r.start_time)} · ${formatDuration(r.run_duration)}`}
            onClick={(e) => e.stopPropagation()}
          />
        );
      })}
    </span>
  );
}

export function useRunNow() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: ({ jobId, params }: { jobId: number; params?: Record<string, string> }) =>
      post<{ run_id: number }>("/api/2.2/jobs/run-now", {
        job_id: jobId,
        ...(params && Object.keys(params).length ? { job_parameters: params } : {}),
      }).then((r) => ({ ...r, jobId })),
    onSuccess: ({ run_id, jobId }) => {
      qc.invalidateQueries({ queryKey: ["job-runs", jobId] });
      qc.invalidateQueries({ queryKey: ["all-runs"] });
      toast.success(`Started run ${run_id}`, { label: "View run", onClick: () => navigate(`/jobs/${jobId}/runs/${run_id}`) });
    },
  });
}

export function RunNowDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const params = job.settings.parameters ?? [];
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(params.map((p) => [p.name, p.default ?? ""])),
  );
  const [extra, setExtra] = useState<{ name: string; value: string }[]>([]);
  const runNow = useRunNow();
  const navigate = useNavigate();

  const submit = () => {
    const all = { ...values };
    for (const e of extra) if (e.name.trim()) all[e.name.trim()] = e.value;
    runNow.mutate(
      { jobId: job.job_id, params: all },
      {
        onSuccess: ({ run_id }) => {
          onClose();
          navigate(`/jobs/${job.job_id}/runs/${run_id}`);
        },
      },
    );
  };

  return (
    <Dialog
      title="Run now with different parameters"
      onClose={onClose}
      onSubmit={submit}
      footer={<DialogButtons onCancel={onClose} confirmLabel="Run" busy={runNow.isPending} />}
    >
      <p className="secondary" style={{ marginTop: 0 }}>
        Values apply to this run only. Defaults come from the job's parameters.
      </p>
      {params.length === 0 && extra.length === 0 && <p className="muted">This job defines no parameters.</p>}
      {params.map((p) => (
        <div className="field" key={p.name}>
          <label className="mono" htmlFor={`p-${p.name}`}>
            {p.name}
          </label>
          <input
            id={`p-${p.name}`}
            className="input"
            value={values[p.name] ?? ""}
            onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
            placeholder={p.default}
          />
        </div>
      ))}
      {extra.map((e, i) => (
        <div className="row" key={i} style={{ marginBottom: 8 }}>
          <input
            className="input mono"
            placeholder="name"
            value={e.name}
            onChange={(ev) => setExtra(extra.map((x, j) => (j === i ? { ...x, name: ev.target.value } : x)))}
            style={{ flex: 1 }}
          />
          <input
            className="input"
            placeholder="value"
            value={e.value}
            onChange={(ev) => setExtra(extra.map((x, j) => (j === i ? { ...x, value: ev.target.value } : x)))}
            style={{ flex: 2 }}
          />
        </div>
      ))}
      <button type="button" className="link-btn" onClick={() => setExtra([...extra, { name: "", value: "" }])}>
        + Add parameter
      </button>
    </Dialog>
  );
}

const JOB_TEMPLATE = `{
  "name": "My job",
  "tasks": [
    {
      "task_key": "main",
      "sql_task": {
        "file": { "path": "/Users/<you>/query.sql" }
      }
    }
  ],
  "parameters": [],
  "max_concurrent_runs": 1
}
`;

export function JobJsonDialog({
  title,
  initial,
  submitLabel,
  onSubmit,
  onClose,
  busy,
}: {
  title: string;
  initial: string;
  submitLabel: string;
  onSubmit: (settings: JobSettings) => void;
  onClose: () => void;
  busy?: boolean;
}) {
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const submit = () => {
    try {
      const parsed = JSON.parse(text) as JobSettings;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("Settings must be a JSON object");
      setError(null);
      onSubmit(parsed);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <Dialog
      title={title}
      wide
      onClose={onClose}
      onSubmit={submit}
      footer={<DialogButtons onCancel={onClose} confirmLabel={submitLabel} busy={busy} />}
    >
      <p className="secondary" style={{ marginTop: 0 }}>
        Job settings in the Databricks Jobs 2.2 format (the same JSON as <span className="mono">jobs/create</span>).
      </p>
      <div style={{ height: 380, border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
        <Editor value={text} onChange={setText} language="json" />
      </div>
      {error && (
        <div className="banner error" style={{ marginTop: 10, marginBottom: 0 }}>
          <div className="banner-body">Invalid JSON: {error}</div>
        </div>
      )}
    </Dialog>
  );
}

export function CreateJobDialog({ onClose, home }: { onClose: () => void; home: string | null }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: (settings: JobSettings) => post<{ job_id: number }>("/api/2.2/jobs/create", settings),
    onSuccess: ({ job_id }) => {
      qc.invalidateQueries({ queryKey: keys.jobs });
      onClose();
      navigate(`/jobs/${job_id}?tab=tasks`);
    },
  });
  return (
    <JobJsonDialog
      title="Create job"
      initial={JOB_TEMPLATE.replace("/Users/<you>", home ?? "/Users/<you>")}
      submitLabel="Create"
      busy={create.isPending}
      onClose={onClose}
      onSubmit={(s) => create.mutate(s)}
    />
  );
}

export function taskTypeLabel(task: Record<string, unknown>): string {
  const t = taskType(task);
  return t ? TASK_TYPE_LABEL[t] ?? t : "Task";
}
