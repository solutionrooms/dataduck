// SQL editor tabs, persisted in localStorage.

import { createStore, useStore } from "./store";

export interface SqlTab {
  id: string;
  title: string;
  sql: string;
  catalog?: string;
  schema?: string;
  warehouseId?: string;
}

export interface SqlTabsState {
  tabs: SqlTab[];
  activeId: string;
}

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function blank(n: number): SqlTab {
  return { id: newId(), title: `New Query ${n}`, sql: "" };
}

const first = blank(1);
const sqlTabsStore = createStore<SqlTabsState>({ tabs: [first], activeId: first.id }, "duckbricks.sql-tabs");

// Repair a stored state that lost its tabs.
if (sqlTabsStore.get().tabs.length === 0) sqlTabsStore.set({ tabs: [first], activeId: first.id });

export function useSqlTabs(): SqlTabsState {
  return useStore(sqlTabsStore);
}

function nextNumber(tabs: SqlTab[]): number {
  const nums = tabs.map((t) => Number(/^New Query (\d+)$/.exec(t.title)?.[1] ?? 0));
  return Math.max(0, ...nums) + 1;
}

export function openSqlTab(sql = "", title?: string, catalog?: string, schema?: string): string {
  const state = sqlTabsStore.get();
  const tab: SqlTab = { ...blank(nextNumber(state.tabs)), sql, catalog, schema };
  if (title) tab.title = title;
  sqlTabsStore.set({ tabs: [...state.tabs, tab], activeId: tab.id });
  return tab.id;
}

export function updateSqlTab(id: string, patch: Partial<SqlTab>): void {
  sqlTabsStore.set((s) => ({ ...s, tabs: s.tabs.map((t) => (t.id === id ? { ...t, ...patch } : t)) }));
}

export function activateSqlTab(id: string): void {
  sqlTabsStore.set((s) => ({ ...s, activeId: id }));
}

export function closeSqlTab(id: string): void {
  sqlTabsStore.set((s) => {
    const idx = s.tabs.findIndex((t) => t.id === id);
    const tabs = s.tabs.filter((t) => t.id !== id);
    if (tabs.length === 0) {
      const t = blank(1);
      return { tabs: [t], activeId: t.id };
    }
    const activeId = s.activeId === id ? tabs[Math.max(0, idx - 1)].id : s.activeId;
    return { tabs, activeId };
  });
}
