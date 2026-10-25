import {
  Ban,
  Boxes,
  Repeat,
  Split,
  SquareTerminal,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  Clock,
  Database,
  Eye,
  File,
  FileCode,
  Folder,
  LoaderCircle,
  NotebookText,
  Play,
  Table2,
  Workflow,
  Layers,
  ScrollText,
} from "lucide-react";
import type { ObjectType } from "../api/types";
import type { RecentKind } from "../lib/recents";
import type { StateInfo } from "../lib/runState";
import { extension } from "../lib/paths";

/** Our own small duck mark (not any vendor logo). */
export function DuckMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="8" fill="#2b7de9" />
      <path
        d="M9 20.5c0-3.2 2.4-5.3 5.6-5.3h.6c-.8-.8-1.2-1.8-1.2-2.9 0-2.4 2-4.3 4.4-4.3s4.4 1.9 4.4 4.3c0 .5-.1 1-.3 1.5l2.6.9-2.8 1.1c.9 1 1.4 2.4 1.4 3.9 0 3.6-3 6.3-7.3 6.3h-1.1C11.6 26 9 23.7 9 20.5Z"
        fill="#f5c443"
      />
      <circle cx="19.6" cy="11.8" r="1.1" fill="#1b1f23" />
      <path d="M12.5 20.6c1.6 1.4 4.7 1.6 6.8.2" stroke="#d99a1e" strokeWidth="1.3" fill="none" strokeLinecap="round" />
    </svg>
  );
}

export function ObjectIcon({ type, path, size = 16 }: { type: ObjectType; path?: string; size?: number }) {
  switch (type) {
    case "DIRECTORY":
    case "REPO":
      return <Folder size={size} className="icon-folder" fill="currentColor" fillOpacity={0.25} />;
    case "NOTEBOOK":
      return <NotebookText size={size} className="icon-notebook" />;
    default: {
      const ext = path ? extension(path) : "";
      if (["py", "sql", "yml", "yaml", "json", "sh", "toml"].includes(ext)) {
        return <FileCode size={size} className="icon-file" />;
      }
      return <File size={size} className="icon-file" />;
    }
  }
}

export function TableIcon({ tableType, size = 16 }: { tableType?: string; size?: number }) {
  if (tableType === "VIEW" || tableType === "MATERIALIZED_VIEW") return <Eye size={size} className="icon-view" />;
  return <Table2 size={size} className="icon-table" />;
}

export function RecentIcon({ kind, size = 16 }: { kind: RecentKind; size?: number }) {
  switch (kind) {
    case "folder":
      return <ObjectIcon type="DIRECTORY" size={size} />;
    case "file":
      return <ObjectIcon type="FILE" path="x.py" size={size} />;
    case "notebook":
      return <ObjectIcon type="NOTEBOOK" size={size} />;
    case "table":
      return <TableIcon size={size} />;
    case "job":
      return <Workflow size={size} className="icon-job" />;
    case "run":
      return <Play size={size} className="icon-job" />;
    case "query":
      return <ScrollText size={size} className="icon-table" />;
    case "pipeline":
      return <Layers size={size} className="icon-job" />;
  }
}

export function CatalogIcon({ size = 16 }: { size?: number }) {
  return <Database size={size} className="icon-folder" />;
}

export function StateIcon({ info, size = 16 }: { info: StateInfo; size?: number }) {
  switch (info.kind) {
    case "success":
      return <CircleCheck size={size} />;
    case "failed":
      return <CircleX size={size} />;
    case "running":
      return <LoaderCircle size={size} className="spin" />;
    case "pending":
    case "queued":
    case "blocked":
      return <Clock size={size} />;
    case "canceled":
      return <Ban size={size} />;
    case "upstream":
      return <CircleMinus size={size} />;
    case "skipped":
    case "excluded":
      return <CircleDashed size={size} />;
  }
}

export function StatusLabel({ info, size = 16 }: { info: StateInfo; size?: number }) {
  return (
    <span className={`status status-${info.kind}`}>
      <StateIcon info={info} size={size} />
      <span className="status-text">{info.label}</span>
    </span>
  );
}

export function TaskTypeIcon({ type, size = 16 }: { type: string | null; size?: number }) {
  switch (type) {
    case "sql_task":
      return <SquareTerminal size={size} className="icon-table" />;
    case "spark_python_task":
    case "python_wheel_task":
      return <FileCode size={size} className="icon-file" />;
    case "notebook_task":
      return <NotebookText size={size} className="icon-notebook" />;
    case "dbt_task":
      return <Boxes size={size} className="icon-notebook" />;
    case "run_job_task":
      return <Workflow size={size} className="icon-job" />;
    case "condition_task":
      return <Split size={size} className="icon-view" />;
    case "pipeline_task":
      return <Layers size={size} className="icon-job" />;
    case "for_each_task":
      return <Repeat size={size} className="icon-view" />;
    default:
      return <Play size={size} className="icon-job" />;
  }
}
