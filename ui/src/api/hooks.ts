// Shared react-query hooks and API helpers.

import { useQuery } from "@tanstack/react-query";
import { get, post } from "./client";
import type {
  Catalog,
  Job,
  NodeType,
  PlatformStatus,
  Schema,
  StatementResponse,
  TableInfo,
  Warehouse,
  WorkspaceObject,
} from "./types";

export const keys = {
  status: ["status"] as const,
  wsList: (path: string) => ["ws-list", path] as const,
  wsFavorites: ["ws-favorites"] as const,
  wsTrash: ["ws-trash"] as const,
  catalogs: ["uc-catalogs"] as const,
  schemas: (catalog: string) => ["uc-schemas", catalog] as const,
  tables: (catalog: string, schema: string) => ["uc-tables", catalog, schema] as const,
  table: (fullName: string) => ["uc-table", fullName] as const,
  jobs: ["jobs"] as const,
  job: (id: number) => ["job", id] as const,
  jobRuns: (id: number, limit: number) => ["job-runs", id, limit] as const,
  run: (id: number) => ["run", id] as const,
  warehouses: ["warehouses"] as const,
  nodeTypes: ["node-types"] as const,
};

export function useStatus() {
  return useQuery({
    queryKey: keys.status,
    queryFn: () => get<PlatformStatus>("/api/duckbricks/status"),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Home folder of the current user, or null until status has loaded. */
export function useHomePath(): string | null {
  const { data } = useStatus();
  return data?.user ? `/Users/${data.user}` : null;
}

export function listWorkspace(path: string) {
  return get<{ objects?: WorkspaceObject[] }>("/api/2.0/workspace/list", { path }).then((r) => r.objects ?? []);
}

export function useWorkspaceList(path: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.wsList(path ?? ""),
    queryFn: () => listWorkspace(path!),
    enabled: enabled && path !== null,
  });
}

export function useFavorites() {
  return useQuery({
    queryKey: keys.wsFavorites,
    queryFn: () =>
      get<{ objects?: WorkspaceObject[] }>("/api/duckbricks/workspace/favorites").then((r) => r.objects ?? []),
  });
}

export function useCatalogs() {
  return useQuery({
    queryKey: keys.catalogs,
    queryFn: () => get<{ catalogs?: Catalog[] }>("/api/2.1/unity-catalog/catalogs").then((r) => r.catalogs ?? []),
    staleTime: 30_000,
  });
}

export function fetchSchemas(catalog: string) {
  return get<{ schemas?: Schema[] }>("/api/2.1/unity-catalog/schemas", { catalog_name: catalog }).then(
    (r) => r.schemas ?? [],
  );
}

export function useSchemas(catalog: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.schemas(catalog ?? ""),
    queryFn: () => fetchSchemas(catalog!),
    enabled: enabled && !!catalog,
    staleTime: 30_000,
  });
}

export function fetchTables(catalog: string, schema: string) {
  return get<{ tables?: TableInfo[] }>("/api/2.1/unity-catalog/tables", {
    catalog_name: catalog,
    schema_name: schema,
  }).then((r) => r.tables ?? []);
}

export function useTables(catalog: string | null | undefined, schema: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: keys.tables(catalog ?? "", schema ?? ""),
    queryFn: () => fetchTables(catalog!, schema!),
    enabled: enabled && !!catalog && !!schema,
    staleTime: 30_000,
  });
}

export function fetchTable(fullName: string) {
  return get<TableInfo>(`/api/2.1/unity-catalog/tables/${encodeURIComponent(fullName)}`);
}

export function useTable(fullName: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.table(fullName ?? ""),
    queryFn: () => fetchTable(fullName!),
    enabled: enabled && !!fullName,
    staleTime: 30_000,
  });
}

/** Every job, following has_more pagination (the API caps a page at 100). */
export async function fetchAllJobs(): Promise<Job[]> {
  const out: Job[] = [];
  for (let offset = 0; offset < 2000; offset += 100) {
    const page = await get<{ jobs?: Job[]; has_more?: boolean }>("/api/2.2/jobs/list", {
      limit: 100,
      offset,
      expand_tasks: true,
    });
    out.push(...(page.jobs ?? []));
    if (!page.has_more) break;
  }
  return out;
}

export function useJobs() {
  return useQuery({ queryKey: keys.jobs, queryFn: fetchAllJobs, staleTime: 10_000 });
}

export function useWarehouses() {
  return useQuery({
    queryKey: keys.warehouses,
    queryFn: () =>
      get<{ warehouses?: Warehouse[] }>("/api/2.0/sql/warehouses").then((r) => r.warehouses ?? []),
    staleTime: 15_000,
    // Poll while a warehouse is changing state.
    refetchInterval: (q) => (q.state.data?.some((w) => w.state === "STARTING" || w.state === "STOPPING") ? 2500 : false),
  });
}

export function useNodeTypes() {
  return useQuery({
    queryKey: keys.nodeTypes,
    queryFn: () =>
      get<{ node_types?: NodeType[] }>("/api/2.1/clusters/list-node-types").then((r) => r.node_types ?? []),
    staleTime: 300_000,
  });
}

// --- SQL statement execution -------------------------------------------------------------

export interface StatementRequest {
  statement: string;
  catalog?: string;
  schema?: string;
  warehouse_id?: string;
  row_limit?: number;
  read_only?: boolean;
}

export interface StatementResult {
  response: StatementResponse;
  rows: (string | null)[][];
  durationMs: number;
}

const ACTIVE_STATES = new Set(["PENDING", "RUNNING"]);

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });
}

/**
 * Run a statement through the Statement Execution API, polling while it is PENDING/RUNNING and
 * collecting every result chunk. `onStarted` receives the statement id so the caller can cancel it.
 */
export async function executeStatement(
  req: StatementRequest,
  opts: { onStarted?: (id: string) => void; signal?: AbortSignal } = {},
): Promise<StatementResult> {
  const started = performance.now();
  let res = await post<StatementResponse>("/api/2.0/sql/statements", {
    ...req,
    wait_timeout: "30s",
    on_wait_timeout: "CONTINUE",
    client: "UI",
  });
  opts.onStarted?.(res.statement_id);
  while (ACTIVE_STATES.has(res.status.state)) {
    await sleep(1000, opts.signal);
    res = await get<StatementResponse>(`/api/2.0/sql/statements/${res.statement_id}`);
  }
  const rows: (string | null)[][] = [...(res.result?.data_array ?? [])];
  if (res.status.state === "SUCCEEDED") {
    const chunks = res.manifest?.total_chunk_count ?? 1;
    for (let i = 1; i < Math.min(chunks, 50); i++) {
      const chunk = await get<{ data_array?: (string | null)[][] }>(
        `/api/2.0/sql/statements/${res.statement_id}/result/chunks/${i}`,
      );
      rows.push(...(chunk.data_array ?? []));
    }
  }
  return { response: res, rows, durationMs: Math.round(performance.now() - started) };
}
