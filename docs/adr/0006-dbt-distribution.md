# 0006. dbt distribution is a build choice; default to the full `dbt` distribution

- Status: accepted (supersedes the open question in ADR 0005)
- Date: 2026-10-25

## Context

ADR 0005 left open whether the worker runs dbt-oss or the full `dbt` distribution. Only the full
distribution has the static-analysis engine that column lineage (SPEC goal 5, Phase 4) and the
strict `dbt_check` need. The owner asked to stay agnostic on the dbt build and use the full one
for now.

## Findings (Spike B rerun, linux/arm64)

| Question | Result |
|---|---|
| Install | `dbt system upgrade-distribution` downloads `https://public.cdn.getdbt.com/fs/cli/fs-v<version>-<arch>-unknown-linux-gnu.tar.gz`. The worker image downloads that tarball directly, checksum-pinned. |
| Version | `dbt 2.0.9` (dbt-oss latest is 2.0.8) |
| DuckDB driver | Uses the same system ADBC driver as dbt-oss: `v2.0.0`, matching the platform |
| Build into DuckLake | Spike B passes unchanged |
| `dbt compile --static-analysis strict --generate-info-schema` | Writes `target/info_schema/v1/dbt.column_lineage.parquet` with `parent_node_unique_id, parent_column_name, child_node_unique_id, child_column_name, evolution` (`copy`, `scan`, `mod`) |
| Strict analysis needs sources to exist | If a source table is missing, dbt warns (dbt1014) and turns static analysis off for dependents |

## Decision

- `docker/worker.Dockerfile` takes `DBT_DISTRIBUTION=full|oss` (default `full`), each with its
  own pinned version and per-arch sha256. The image exports `DBK_DBT_DISTRIBUTION`.
- Platform code never assumes the distribution. Features that need static analysis check the
  distribution at run time: with `oss`, lineage capture is skipped with a log line and
  `dbt_check` reports parse and compile errors only.
- Switch with `docker compose build --build-arg DBT_DISTRIBUTION=oss worker`.

## Consequences

- The full distribution has a proprietary (free to use) licence; dbt-oss stays one build arg away.
- Version bumps update the matching `DBT_*_VERSION` and both checksums.
