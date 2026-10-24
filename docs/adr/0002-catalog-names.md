# 0002. Catalog names: `workspace` replaces `main`; `system` is reserved

- Status: accepted
- Date: 2026-10-25

## Context

SPEC.md bootstraps catalogs `main`, `dev` and `prod`, and Phase 4 adds a `system` catalog. In
DuckDB 2.0.0 both names fail at attach time:

```
ATTACH 'ducklake:...' AS main   -> Binder Error: Attached database name "main" cannot be used because it is a reserved name
ATTACH 'ducklake:...' AS system -> Binder Error: Attached database name "system" cannot be used because it is a reserved name
```

`temp` and `memory` are also built-in database names. A catalog's name must equal its DuckDB
attach alias, or every SQL statement that names it would need rewriting.

## Options

1. Keep `main` in the API and attach it under another alias (`main_`). SQL that says `main.x.y`
   breaks, so this needs SQL rewriting: a large workaround.
2. Bootstrap `workspace` instead of `main`. Workspaces with Unity Catalog enabled automatically
   get a workspace catalog as their default (in Databricks Free Edition it is named
   `workspace`), so this stays Databricks-shaped. Not checked against current docs.
3. Drop the default catalog and bootstrap only `dev` and `prod`.

## Decision

Option 2. Bootstrap `workspace`, `dev` and `prod`; `workspace` is the default catalog. The registry
rejects `main`, `system`, `temp`, `memory`, `public`, `information_schema` and `pg_*` names.

For `system` (Phase 4): attach the system tables under an internal alias and present it as
`system` in the Unity Catalog API. Decide the alias and how `sql_query` exposes it when Phase 4
starts.

## Consequences

- Bundles that hard-code `main` need a variable or a different catalog. Recorded in compat.md.
- Example bundle targets use `dev` and `prod`, which are unaffected.
