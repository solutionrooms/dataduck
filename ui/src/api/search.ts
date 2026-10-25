// Indexes behind the top search box: workspace objects (recursive), tables, jobs.

import { useQuery } from "@tanstack/react-query";
import { fetchSchemas, fetchTables, listWorkspace } from "./hooks";
import { get } from "./client";
import type { Catalog, TableInfo, WorkspaceObject } from "./types";

const MAX_OBJECTS = 3000;
const MAX_DIRS = 400;

/** Breadth-first listing of the given roots, bounded so a huge tree cannot stall the UI. */
export async function crawlWorkspace(roots: string[]): Promise<WorkspaceObject[]> {
  const out: WorkspaceObject[] = [];
  const queue = [...roots];
  let dirs = 0;
  while (queue.length && dirs < MAX_DIRS && out.length < MAX_OBJECTS) {
    const batch = queue.splice(0, 8);
    dirs += batch.length;
    const results = await Promise.all(batch.map((p) => listWorkspace(p).catch(() => [] as WorkspaceObject[])));
    for (const objects of results) {
      for (const o of objects) {
        out.push(o);
        if (o.object_type === "DIRECTORY") queue.push(o.path);
      }
    }
  }
  return out;
}

export function useWorkspaceIndex(home: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["search-index", "workspace", home],
    queryFn: () => crawlWorkspace(home ? [home, "/Shared"] : ["/Shared"]),
    enabled: enabled && home !== null,
    staleTime: 60_000,
  });
}

export async function crawlTables(): Promise<TableInfo[]> {
  const { catalogs = [] } = await get<{ catalogs?: Catalog[] }>("/api/2.1/unity-catalog/catalogs");
  const schemaLists = await Promise.all(catalogs.map((c) => fetchSchemas(c.name).catch(() => [])));
  const pairs = schemaLists.flatMap((schemas, i) => schemas.map((s) => [catalogs[i].name, s.name] as const));
  const tableLists = await Promise.all(pairs.map(([c, s]) => fetchTables(c, s).catch(() => [] as TableInfo[])));
  return tableLists.flat();
}

export function useTableIndex(enabled: boolean) {
  return useQuery({
    queryKey: ["search-index", "tables"],
    queryFn: crawlTables,
    enabled,
    staleTime: 60_000,
  });
}
