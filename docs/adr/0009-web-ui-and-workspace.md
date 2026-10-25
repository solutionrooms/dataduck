# 0009. A Databricks-style web UI and the Workspace API are in scope

- Status: accepted (amends SPEC section 1 non-goals)
- Date: 2026-10-25

## Context

SPEC section 1 listed "a custom web UI beyond notebooks and the DuckDB UI" as a non-goal. The
owner asked for the platform to look like the Databricks workspace (screenshot of the Azure
Databricks workspace browser) and for workspace features.

## Decision

- A React single-page app in `ui/` (React 19, Vite, TypeScript), built in the `ui` stage of
  `docker/Dockerfile` and served by the API at `/`. It talks only to the public API, like every
  other client. Pages: workspace browser (tree, favorites, trash), file editor, catalog explorer
  with lineage, SQL editor and query history, SQL warehouses, jobs and pipelines, run detail,
  compute.
- The Databricks Workspace API subset (`/api/2.0/workspace/list|get-status|mkdirs|import|export|
  delete`) over a workspace volume, plus Duckbricks endpoints for trash and favorites. Bundles
  deploy into the same tree (`/Users/<you>/.bundle/...`).
- Login takes the platform token (`DBK_TOKEN`) and keeps it in the browser's local storage.

## Consequences

- The UI is a second large codebase; it has a type-check and build gate (`npm run build`) but no
  unit tests yet.
- The non-goal line in SPEC.md points here.
