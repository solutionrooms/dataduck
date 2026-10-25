// Global toast queue: callable from anywhere (mutation error handlers included).

import { errorMessage } from "../api/client";
import { createStore, useStore } from "./store";

export interface Toast {
  id: number;
  kind: "error" | "success" | "info";
  message: string;
  action?: { label: string; href?: string; onClick?: () => void };
}

const toastStore = createStore<Toast[]>([]);
let nextId = 1;

export function dismissToast(id: number): void {
  toastStore.set((prev) => prev.filter((t) => t.id !== id));
}

function push(t: Omit<Toast, "id">, ttl: number): void {
  const id = nextId++;
  toastStore.set((prev) => [...prev.slice(-4), { ...t, id }]);
  window.setTimeout(() => dismissToast(id), ttl);
}

export const toast = {
  error: (err: unknown) => push({ kind: "error", message: errorMessage(err) }, 8000),
  success: (message: string, action?: Toast["action"]) => push({ kind: "success", message, action }, action ? 10_000 : 5000),
  info: (message: string, action?: Toast["action"]) => push({ kind: "info", message, action }, action ? 10_000 : 5000),
};

export function useToasts(): Toast[] {
  return useStore(toastStore);
}
