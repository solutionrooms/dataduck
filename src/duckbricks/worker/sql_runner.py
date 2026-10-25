"""Runs one `sql_task` file in a fresh process: `python -m duckbricks.worker.sql_runner`.

Input comes from `DBK_SQL_TASK` (JSON: path, parameters, node_type). Each statement binds only
the parameters it uses (ADR 0001 #9). `:name` markers are accepted as well as DuckDB's `$name`.
The parameters `catalog` and `schema`, when given, also set the session's default namespace.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from duckbricks.core.statements import replace_markers, to_json_value

PREVIEW_ROWS = 10


def main() -> int:
    spec: dict[str, Any] = json.loads(os.environ["DBK_SQL_TASK"])
    params: dict[str, str] = {k: str(v) for k, v in (spec.get("parameters") or {}).items()}
    sql = Path(spec["path"]).read_text()
    # :name -> $name, only for names that are parameters, so `::` casts are untouched.
    sql = replace_markers(sql, set(params))

    from duckbricks.core.session import open_session

    con = open_session(
        spec.get("node_type"),
        default_catalog=params.get("catalog") or "workspace",
        default_schema=params.get("schema"),
    )
    statements = con.extract_statements(sql)
    print(f"Running {len(statements)} statement(s) from {spec['workspace_path']}", flush=True)
    outcome: dict[str, Any] = {"sql_output": {"statements": len(statements)}}
    for i, statement in enumerate(statements, start=1):
        bound = {name: params[name] for name in statement.named_parameters if name in params}
        missing = sorted(set(statement.named_parameters) - set(bound))
        text = statement.query.strip()
        print(f"\n-- [{i}/{len(statements)}] {text[:2000]}", flush=True)
        if missing:
            raise ValueError(
                f"Statement {i} uses parameter(s) {', '.join(missing)} that the task does not set"
            )
        started = time.monotonic()
        relation = con.execute(statement.query, bound) if bound else con.execute(statement.query)
        elapsed = time.monotonic() - started
        if relation.description:
            columns = [d[0] for d in relation.description]
            rows = relation.fetchmany(PREVIEW_ROWS)
            print(f"   {' | '.join(columns)}")
            for row in rows:
                print(f"   {' | '.join(str(to_json_value(v)) for v in row)}")
        print(f"   ok ({elapsed:.2f}s)", flush=True)
    con.close()
    target = os.environ.get("DBK_TASK_OUTCOME")
    if target:
        Path(target).write_text(json.dumps(outcome))
    return 0


def run() -> int:
    try:
        return main()
    except Exception as exc:
        message = str(exc).strip()
        print(f"\nError: {message}", flush=True)
        target = os.environ.get("DBK_TASK_OUTCOME")
        if target:
            Path(target).write_text(json.dumps({"error": message.splitlines()[0]}))
        return 1


if __name__ == "__main__":
    sys.exit(run())
