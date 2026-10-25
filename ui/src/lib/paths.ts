// Workspace path helpers.

import type { WorkspaceObject } from "../api/types";

export function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  return i >= 0 ? trimmed.slice(i + 1) || "/" : trimmed;
}

export function dirname(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  return i > 0 ? trimmed.slice(0, i) : "/";
}

export function joinPath(dir: string, name: string): string {
  return `${dir.replace(/\/+$/, "")}/${name.replace(/^\/+/, "")}`;
}

/** Encode a workspace path for use in an in-app route, keeping the slashes readable. */
export function encodePath(path: string): string {
  return path
    .split("/")
    .map((s) => encodeURIComponent(s))
    .join("/")
    .replace(/^\/+/, "");
}

export function browseUrl(path: string): string {
  const enc = encodePath(path);
  return enc ? `/browse/${enc}` : "/browse";
}

export function editorUrl(path: string): string {
  return `/editor/${encodePath(path)}`;
}

/** Path from a router splat (`/browse/*`), already decoded by the router. */
export function pathFromSplat(splat: string | undefined): string {
  const clean = (splat ?? "").replace(/^\/+|\/+$/g, "");
  return clean ? `/${clean}` : "/";
}

/** marimo URL for a notebook: `${links.notebooks}/?file=<path relative to workspace root>`. */
export function notebookUrl(base: string | undefined, path: string): string {
  const root = (base || "http://127.0.0.1:2718").replace(/\/+$/, "");
  const rel = encodeURIComponent(path.replace(/^\/+/, "")).replace(/%2F/g, "/");
  return `${root}/?file=${rel}`;
}

export function extension(path: string): string {
  const name = basename(path);
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

export type EditorLanguage = "sql" | "python" | "yaml" | "json" | "markdown" | "text";

export function languageForPath(path: string): EditorLanguage {
  switch (extension(path)) {
    case "sql":
      return "sql";
    case "py":
      return "python";
    case "yml":
    case "yaml":
      return "yaml";
    case "json":
    case "ipynb":
      return "json";
    case "md":
    case "markdown":
      return "markdown";
    default:
      return "text";
  }
}

export function objectTypeLabel(o: Pick<WorkspaceObject, "object_type">): string {
  switch (o.object_type) {
    case "DIRECTORY":
      return "Folder";
    case "NOTEBOOK":
      return "Notebook";
    case "FILE":
      return "File";
    case "REPO":
      return "Repo";
    default:
      return o.object_type;
  }
}

export const MARIMO_TEMPLATE = `import marimo

app = marimo.App()


@app.cell
def _():
    from duckbricks.runtime import con
    return (con,)


if __name__ == "__main__":
    app.run()
`;
