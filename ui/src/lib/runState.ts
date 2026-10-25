// Map Jobs run states onto a small set of display kinds.

import type { RunState } from "../api/types";

export type StateKind =
  | "success"
  | "failed"
  | "running"
  | "pending"
  | "queued"
  | "canceled"
  | "skipped"
  | "excluded"
  | "blocked"
  | "upstream";

export interface StateInfo {
  kind: StateKind;
  label: string;
  active: boolean;
}

const ACTIVE = new Set(["QUEUED", "BLOCKED", "PENDING", "RUNNING", "TERMINATING", "WAITING_FOR_RETRY"]);

export function isActive(state: RunState | undefined | null): boolean {
  return !!state && ACTIVE.has(state.life_cycle_state);
}

export function stateInfo(state: RunState | undefined | null): StateInfo {
  if (!state) return { kind: "pending", label: "Pending", active: true };
  const lc = state.life_cycle_state;
  const rs = state.result_state;
  if (rs) {
    switch (rs) {
      case "SUCCESS":
      case "SUCCESS_WITH_FAILURES":
        return { kind: "success", label: rs === "SUCCESS" ? "Succeeded" : "Succeeded with failures", active: false };
      case "FAILED":
        return { kind: "failed", label: "Failed", active: false };
      case "TIMEDOUT":
        return { kind: "failed", label: "Timed out", active: false };
      case "CANCELED":
        return { kind: "canceled", label: "Canceled", active: false };
      case "UPSTREAM_FAILED":
        return { kind: "upstream", label: "Upstream failed", active: false };
      case "UPSTREAM_CANCELED":
        return { kind: "upstream", label: "Upstream canceled", active: false };
      case "EXCLUDED":
        return { kind: "excluded", label: "Excluded", active: false };
      case "SKIPPED":
        return { kind: "skipped", label: "Skipped", active: false };
      case "MAXIMUM_CONCURRENT_RUNS_REACHED":
        return { kind: "skipped", label: "Skipped (max concurrent runs)", active: false };
      default:
        if (!ACTIVE.has(lc)) return { kind: "failed", label: titleCase(rs), active: false };
    }
  }
  switch (lc) {
    case "RUNNING":
      return { kind: "running", label: "Running", active: true };
    case "TERMINATING":
      return { kind: "running", label: "Terminating", active: true };
    case "PENDING":
      return { kind: "pending", label: "Pending", active: true };
    case "QUEUED":
      return { kind: "queued", label: "Queued", active: true };
    case "WAITING_FOR_RETRY":
      return { kind: "pending", label: "Waiting for retry", active: true };
    case "BLOCKED":
      return { kind: "blocked", label: "Blocked", active: true };
    case "SKIPPED":
      return { kind: "skipped", label: "Skipped", active: false };
    case "INTERNAL_ERROR":
      return { kind: "failed", label: "Internal error", active: false };
    case "TERMINATED":
      return { kind: "success", label: "Terminated", active: false };
    default:
      return { kind: "pending", label: titleCase(lc), active: ACTIVE.has(lc) };
  }
}

export function titleCase(s: string): string {
  return s
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function triggerLabel(trigger: string | undefined): string {
  switch (trigger) {
    case "ONE_TIME":
      return "Manually";
    case "PERIODIC":
      return "By scheduler";
    case "RETRY":
      return "Retry";
    case "RUN_JOB_TASK":
      return "By a parent job";
    case "FILE_ARRIVAL":
      return "File arrival";
    case "TABLE":
      return "Table update";
    case undefined:
      return "–";
    default:
      return titleCase(trigger);
  }
}

/** Which task type a task settings object carries. */
export const TASK_TYPES = [
  "sql_task",
  "spark_python_task",
  "python_wheel_task",
  "notebook_task",
  "dbt_task",
  "run_job_task",
  "condition_task",
  "pipeline_task",
  "for_each_task",
] as const;

export function taskType(task: Record<string, unknown>): string | null {
  for (const t of TASK_TYPES) if (task[t] !== undefined) return t;
  return null;
}

export const TASK_TYPE_LABEL: Record<string, string> = {
  sql_task: "SQL",
  spark_python_task: "Python script",
  python_wheel_task: "Python wheel",
  notebook_task: "Notebook",
  dbt_task: "dbt",
  run_job_task: "Run job",
  condition_task: "If/else condition",
  pipeline_task: "Pipeline",
  for_each_task: "For each",
};

/** One-line summary of what a task runs. */
export function taskSummary(task: Record<string, unknown>): string {
  const t = taskType(task);
  if (!t) return "";
  const cfg = (task[t] ?? {}) as Record<string, any>;
  switch (t) {
    case "sql_task":
      return cfg.file?.path ?? cfg.query?.query_id ?? (cfg.alert ? "alert" : "");
    case "spark_python_task":
      return cfg.python_file ?? "";
    case "notebook_task":
      return cfg.notebook_path ?? "";
    case "dbt_task":
      return Array.isArray(cfg.commands) ? cfg.commands.join(" && ") : "";
    case "run_job_task":
      return cfg.job_id !== undefined ? `job ${cfg.job_id}` : "";
    case "condition_task":
      return `${cfg.left ?? ""} ${cfg.op ?? ""} ${cfg.right ?? ""}`.trim();
    case "pipeline_task":
      return cfg.pipeline_id ?? "";
    default:
      return "";
  }
}
