# 0003. Catalog metadata isolation: one Postgres schema per catalog

- Status: accepted
- Date: 2026-10-25

## Context

SPEC.md section 16 leaves open whether each catalog's DuckLake metadata lives in its own Postgres
schema or its own database.

## Options

1. Schema per catalog inside the `lake` database.
2. Database per catalog.

## Findings (DuckDB 2.0.0, DuckLake format 1.1)

- `ATTACH ... (METADATA_SCHEMA '<catalog>')` puts the metadata tables in that Postgres schema and
  creates the schema on first attach.
- An unnamed `postgres` secret is not used by DuckLake's metadata connection. Pointing at a named
  secret works: `METADATA_PARAMETERS MAP {'SECRET': 'dbk_pg'}`. The password never appears in
  the ATTACH string.
- Verified: two catalogs side by side in `lake`, `READ_ONLY` attaches rejecting `CREATE` and
  `INSERT`, data files under `s3://lakehouse/<catalog>/<schema>/<table>/`.

```sql
CREATE TEMPORARY SECRET dbk_pg (TYPE postgres, HOST 'postgres', PORT 5432, USER '...', PASSWORD '...');
ATTACH IF NOT EXISTS 'ducklake:postgres:dbname=lake' AS "dev" (
  DATA_PATH 's3://lakehouse/dev/',
  METADATA_SCHEMA 'dev',
  METADATA_PARAMETERS MAP {'SECRET': 'dbk_pg'}
);
```

## Decision

Option 1, schema per catalog. One database to back up, one connection secret, and the attach
option is first-class.

## Consequences

Catalog names double as Postgres schema names, so Postgres-reserved names are rejected (ADR 0002).
