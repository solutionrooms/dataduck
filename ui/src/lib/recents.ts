// The last ~30 opened objects, kept in localStorage.

import { createStore, useStore } from "./store";

export type RecentKind = "folder" | "file" | "notebook" | "table" | "job" | "run" | "query" | "pipeline";

export interface RecentItem {
  kind: RecentKind;
  title: string;
  /** In-app route, e.g. /jobs/12 */
  url: string;
  /** External URL (notebooks open in marimo). */
  href?: string;
  subtitle?: string;
  openedAt: number;
}

const MAX = 30;
const recentsStore = createStore<RecentItem[]>([], "duckbricks.recents");

export function addRecent(item: Omit<RecentItem, "openedAt">): void {
  recentsStore.set((prev) => {
    const rest = prev.filter((r) => !(r.kind === item.kind && r.url === item.url));
    return [{ ...item, openedAt: Date.now() }, ...rest].slice(0, MAX);
  });
}

export function clearRecents(): void {
  recentsStore.set([]);
}

export function useRecents(): RecentItem[] {
  return useStore(recentsStore);
}

export const recentKindLabel: Record<RecentKind, string> = {
  folder: "Folder",
  file: "File",
  notebook: "Notebook",
  table: "Table",
  job: "Job",
  run: "Job run",
  query: "Query",
  pipeline: "Pipeline",
};
