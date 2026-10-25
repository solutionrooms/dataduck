// Response shapes for the endpoints the UI uses. Times are epoch milliseconds.

export interface PlatformStatus {
  status: string;
  user: string;
  versions: { duckdb: string; extensions: Record<string, string> };
  catalogs: { name: string; comment?: string | null; data_path?: string }[];
  workers: Worker[];
  warehouses: { id: string; name: string; state: string }[];
  queue: { pending_tasks: number; running_tasks: number };
  version_mismatches: string[];
  links: { ui?: string; notebooks?: string };
}

export interface Worker {
  worker_id: string;
  alive: boolean;
  slots: number;
  duckdb_version?: string;
  dbt_version?: string;
  dbt_distribution?: string;
  dbt_duckdb_version?: string;
  heartbeat_at?: number;
}

// --- workspace ---------------------------------------------------------------------------

export type ObjectType = "DIRECTORY" | "FILE" | "NOTEBOOK" | "REPO" | "LIBRARY";

export interface WorkspaceObject {
  path: string;
  object_type: ObjectType;
  object_id?: number;
  created_at?: number;
  modified_at?: number;
  size?: number;
  language?: string;
}

export interface TrashItem {
  trash_id: string;
  path: string;
  object_type: ObjectType;
  deleted_at?: number;
}

// --- unity catalog -----------------------------------------------------------------------

export interface Catalog {
  name: string;
  full_name?: string;
  comment?: string | null;
  owner?: string;
  catalog_type?: string;
  storage_root?: string;
  created_at?: number;
  created_by?: string;
  properties?: Record<string, unknown>;
}

export interface Schema {
  name: string;
  catalog_name?: string;
  full_name: string;
  comment?: string | null;
  owner?: string;
  catalog_type?: string;
}

export interface ColumnInfo {
  name: string;
  type_text: string;
  type_name?: string;
  position?: number;
  nullable?: boolean;
  comment?: string | null;
}

export interface TableInfo {
  name: string;
  catalog_name?: string;
  schema_name?: string;
  full_name: string;
  table_type?: string;
  data_source_format?: string | null;
  comment?: string | null;
  owner?: string;
  properties?: Record<string, unknown>;
  columns?: ColumnInfo[];
  storage_location?: string | null;
  view_definition?: string | null;
  created_at?: number;
  updated_at?: number;
}

export interface TableHistoryEntry {
  version: number;
  timestamp: number | string;
  operation: string;
  [key: string]: unknown;
}

export interface TableLineage {
  table: string;
  upstreams: { name: string; depth?: number; via?: string; source?: string }[];
  downstreams: { name: string; depth?: number; via?: string; source?: string }[];
}

export interface ColumnLineage {
  upstreams: { table: string; column: string; depth?: number; evolution?: string; via?: string }[];
  downstreams: { table: string; column: string; depth?: number; evolution?: string; via?: string }[];
}

// --- SQL ---------------------------------------------------------------------------------

export type StatementState = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELED" | "CLOSED";

export interface StatementResponse {
  statement_id: string;
  status: { state: StatementState; error?: { error_code?: string; message: string } };
  manifest?: {
    schema: { column_count?: number; columns: { name: string; type_text: string; type_name?: string }[] };
    total_row_count?: number;
    total_chunk_count?: number;
    truncated?: boolean;
  };
  result?: { chunk_index?: number; data_array?: (string | null)[][]; next_chunk_index?: number };
}

export interface QueryHistoryEntry {
  query_id: string;
  query_text: string;
  status?: string;
  statement_status?: string;
  warehouse_id?: string | null;
  catalog?: string | null;
  schema?: string | null;
  client_application?: string | null;
  error_message?: string | null;
  rows_produced?: number | null;
  query_start_time_ms?: number;
  query_end_time_ms?: number | null;
  duration?: number | null;
}

export interface Warehouse {
  id: string;
  name: string;
  cluster_size?: string;
  auto_stop_mins?: number;
  state: "STOPPED" | "STARTING" | "RUNNING" | "STOPPING" | "DELETING" | "DELETED" | string;
  odbc_params?: { hostname?: string; port?: number; path?: string; protocol?: string };
  num_clusters?: number;
  creator_name?: string;
}

export interface NodeType {
  node_type_id: string;
  description: string;
  memory_limit?: string;
  num_cores?: number;
  is_default?: boolean;
}

// --- jobs --------------------------------------------------------------------------------

export interface TaskDependency {
  task_key: string;
  outcome?: string;
}

export interface TaskSettings {
  task_key: string;
  description?: string;
  depends_on?: TaskDependency[];
  run_if?: string;
  [key: string]: unknown;
}

export interface JobParameter {
  name: string;
  default?: string;
}

export interface JobSettings {
  name?: string;
  tasks?: TaskSettings[];
  schedule?: { quartz_cron_expression: string; timezone_id?: string; pause_status?: string };
  trigger?: { pause_status?: string; periodic?: { interval: number; unit: string }; [k: string]: unknown };
  parameters?: JobParameter[];
  tags?: Record<string, string>;
  max_concurrent_runs?: number;
  [key: string]: unknown;
}

export interface Job {
  job_id: number;
  created_time?: number;
  creator_user_name?: string;
  run_as_user_name?: string;
  settings: JobSettings;
}

export type LifeCycleState =
  | "QUEUED"
  | "BLOCKED"
  | "PENDING"
  | "RUNNING"
  | "TERMINATING"
  | "TERMINATED"
  | "SKIPPED"
  | "INTERNAL_ERROR"
  | "WAITING_FOR_RETRY";

export interface RunState {
  life_cycle_state: LifeCycleState | string;
  result_state?: string;
  state_message?: string;
  user_cancelled_or_timedout?: boolean;
}

export interface TaskRun extends TaskSettings {
  run_id: number;
  state: RunState;
  start_time?: number;
  end_time?: number;
  execution_duration?: number;
  attempt_number?: number;
  run_page_url?: string;
}

export interface Run {
  job_id: number;
  run_id: number;
  run_name?: string;
  trigger?: string;
  run_type?: string;
  creator_user_name?: string;
  state: RunState;
  start_time?: number;
  end_time?: number;
  run_duration?: number;
  execution_duration?: number;
  job_parameters?: { name: string; value?: string; default?: string }[];
  tasks?: TaskRun[];
  run_page_url?: string;
}

export interface RunOutput {
  logs?: string;
  logs_truncated?: boolean;
  error?: string;
  error_trace?: string;
  metadata?: TaskRun;
}

// --- pipelines, bundles ------------------------------------------------------------------

export interface PipelineUpdateSummary {
  update_id: string;
  state: string;
  creation_time?: number | string;
}

export interface PipelineStatus {
  pipeline_id: string;
  name: string;
  state?: string;
  creator_user_name?: string;
  latest_updates?: PipelineUpdateSummary[];
  spec?: Record<string, unknown>;
}

export interface PipelineEvent {
  id: string;
  timestamp: number | string;
  level?: string;
  event_type?: string;
  message?: string;
  details?: unknown;
}

export interface BundleDeployment {
  bundle_name: string;
  target: string;
  root_path?: string;
  mode?: string;
  deployed_at?: number;
  resources?: { resource_type: string; resource_key: string; resource_id: string; url?: string; deployed_at?: number }[];
}
