// Thin fetch wrapper: bearer token, Databricks-style errors, 401 -> login.

const TOKEN_KEY = "duckbricks.token";

export class ApiError extends Error {
  readonly status: number;
  readonly errorCode: string;

  constructor(status: number, errorCode: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = errorCode;
  }
}

/** True when the endpoint does not exist yet (the backend answers 404 ENDPOINT_NOT_FOUND). */
export function isUnavailable(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.errorCode === "ENDPOINT_NOT_FOUND";
}

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  return safeStorage()?.getItem(TOKEN_KEY) ?? null;
}

export function setToken(token: string): void {
  safeStorage()?.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  safeStorage()?.removeItem(TOKEN_KEY);
}

/** Accept `#token=<value>` on first load: store it, then strip the fragment. */
export function consumeTokenFromHash(): void {
  const hash = window.location.hash;
  if (!hash.startsWith("#")) return;
  const params = new URLSearchParams(hash.slice(1));
  const token = params.get("token");
  if (!token) return;
  setToken(token);
  params.delete("token");
  const rest = params.toString();
  const url = window.location.pathname + window.location.search + (rest ? `#${rest}` : "");
  window.history.replaceState(window.history.state, "", url);
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export interface RequestOptions {
  method?: "GET" | "POST" | "DELETE" | "PATCH" | "PUT";
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  token?: string;
}

function buildUrl(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = options.token ?? getToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? (body !== undefined ? "POST" : "GET"),
      headers,
      body,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the Duckbricks API. Is the stack running?");
  }
  const text = await response.text();
  let payload: unknown = undefined;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = undefined;
    }
  }
  if (!response.ok) {
    const p = (payload ?? {}) as { error_code?: string; message?: string; detail?: unknown };
    const code = p.error_code ?? `HTTP_${response.status}`;
    const message =
      p.message ??
      (typeof p.detail === "string" ? p.detail : undefined) ??
      `${response.status} ${response.statusText || "Request failed"}`;
    if (response.status === 401 && !options.token) {
      clearToken();
      unauthorizedListeners.forEach((l) => l());
    }
    throw new ApiError(response.status, code, message);
  }
  return (payload ?? {}) as T;
}

export const get = <T>(path: string, query?: Query, signal?: AbortSignal) =>
  api<T>(path, { method: "GET", query, signal });

export const post = <T>(path: string, body: unknown = {}) => api<T>(path, { method: "POST", body });

export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

// --- base64 helpers (UTF-8 safe) ---------------------------------------------------------

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

export function textToBase64(text: string): string {
  return bytesToBase64(new TextEncoder().encode(text));
}

export function base64ToText(b64: string): string {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}
