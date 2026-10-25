"""Lineage: load dbt's info-schema output, answer upstream/downstream questions.

Table and column lineage live in the control database and appear as `sys.lineage.*` views
(ADR 0007). Each dbt run replaces the edges for the relations it produced.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, delete, insert, or_, select

from duckbricks.core import db
from duckbricks.core.session import open_session, sql_literal

NODE_FILES = ("models", "sources", "seeds", "snapshots")


def _relation(database: str | None, schema: str | None, identifier: str | None) -> str | None:
    if not (database and schema and identifier):
        return None
    return f"{database}.{schema}.{identifier}"


def load_dbt_lineage(
    engine: Engine, info_schema: Path, entity_id: str, run_id: str
) -> dict[str, int]:
    con = open_session("dbk.small", catalogs=[])
    try:
        nodes: dict[str, str] = {}
        for name in NODE_FILES:
            path = info_schema / f"dbt.{name}.parquet"
            if not path.exists():
                continue
            for unique_id, database, schema, identifier, alias in con.execute(
                "SELECT unique_id, database_name, schema_name, identifier, alias "
                f"FROM read_parquet({sql_literal(str(path))})"
            ).fetchall():
                relation = _relation(database, schema, identifier or alias)
                if relation:
                    nodes[unique_id] = relation
        edges: list[tuple[str, str]] = []
        edges_path = info_schema / "dbt.edges.parquet"
        if edges_path.exists():
            for parent, child in con.execute(
                "SELECT parent_unique_id, child_unique_id "
                f"FROM read_parquet({sql_literal(str(edges_path))})"
            ).fetchall():
                if parent in nodes and child in nodes and child.split(".")[0] != "source":
                    edges.append((nodes[parent], nodes[child]))
        columns: list[tuple[str, str, str, str, str | None]] = []
        column_path = info_schema / "dbt.column_lineage.parquet"
        if column_path.exists():
            for parent, parent_col, child, child_col, evolution in con.execute(
                "SELECT parent_node_unique_id, parent_column_name, child_node_unique_id, "
                "child_column_name, evolution "
                f"FROM read_parquet({sql_literal(str(column_path))})"
            ).fetchall():
                if parent in nodes and child in nodes:
                    columns.append((nodes[parent], parent_col, nodes[child], child_col, evolution))
    finally:
        con.close()

    targets = sorted({child for _, child in edges} | {c[2] for c in columns})
    with engine.begin() as conn:
        if targets:
            conn.execute(
                delete(db.table_lineage).where(
                    db.table_lineage.c.target_table_full_name.in_(targets),
                    db.table_lineage.c.source_type == "dbt",
                )
            )
            conn.execute(
                delete(db.column_lineage).where(
                    db.column_lineage.c.target_table_full_name.in_(targets),
                    db.column_lineage.c.source_type == "dbt",
                )
            )
        unique_edges = sorted(set(edges))
        if unique_edges:
            conn.execute(
                insert(db.table_lineage),
                [
                    {
                        "source_table_full_name": s,
                        "target_table_full_name": t,
                        "source_type": "dbt",
                        "entity_id": entity_id,
                        "entity_run_id": run_id,
                    }
                    for s, t in unique_edges
                ],
            )
        if columns:
            conn.execute(
                insert(db.column_lineage),
                [
                    {
                        "source_table_full_name": s,
                        "source_column_name": sc,
                        "target_table_full_name": t,
                        "target_column_name": tc,
                        "evolution": ev,
                        "source_type": "dbt",
                        "entity_id": entity_id,
                        "entity_run_id": run_id,
                    }
                    for s, sc, t, tc, ev in columns
                ],
            )
    return {"tables": len(set(edges)), "columns": len(columns)}


def record_table_edges(
    engine: Engine, edges: list[tuple[str, str]], source_type: str, entity_id: str, run_id: str
) -> None:
    """Replace lineage for the given targets (used by pipelines)."""
    targets = sorted({t for _, t in edges})
    with engine.begin() as conn:
        if targets:
            conn.execute(
                delete(db.table_lineage).where(
                    db.table_lineage.c.target_table_full_name.in_(targets),
                    db.table_lineage.c.source_type == source_type,
                )
            )
        if edges:
            conn.execute(
                insert(db.table_lineage),
                [
                    {
                        "source_table_full_name": s,
                        "target_table_full_name": t,
                        "source_type": source_type,
                        "entity_id": entity_id,
                        "entity_run_id": run_id,
                    }
                    for s, t in sorted(set(edges))
                ],
            )


def table_lineage(engine: Engine, table: str, max_depth: int = 10) -> dict[str, Any]:
    """Upstream and downstream tables, breadth-first, with the depth of each hop."""
    tl = db.table_lineage
    with engine.connect() as conn:
        rows = conn.execute(
            select(tl.c.source_table_full_name, tl.c.target_table_full_name, tl.c.source_type)
        ).all()
    up: dict[str, list[tuple[str, str]]] = {}
    down: dict[str, list[tuple[str, str]]] = {}
    for source, target, kind in rows:
        up.setdefault(target, []).append((source, kind))
        down.setdefault(source, []).append((target, kind))

    def walk(graph: dict[str, list[tuple[str, str]]]) -> list[dict[str, Any]]:
        seen = {table}
        out = []
        queue = deque([(table, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for neighbour, kind in graph.get(node, []):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                out.append({"name": neighbour, "depth": depth + 1, "via": node, "source": kind})
                queue.append((neighbour, depth + 1))
        return out

    return {"table": table, "upstreams": walk(up), "downstreams": walk(down)}


def column_lineage(engine: Engine, table: str, column: str, max_depth: int = 10) -> dict[str, Any]:
    cl = db.column_lineage
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                cl.c.source_table_full_name,
                cl.c.source_column_name,
                cl.c.target_table_full_name,
                cl.c.target_column_name,
                cl.c.evolution,
            )
        ).all()
    up: dict[tuple[str, str], list[tuple[str, str, str | None]]] = {}
    down: dict[tuple[str, str], list[tuple[str, str, str | None]]] = {}
    for st, sc, tt, tc, ev in rows:
        up.setdefault((tt, tc), []).append((st, sc, ev))
        down.setdefault((st, sc), []).append((tt, tc, ev))

    def walk(graph: dict[tuple[str, str], list[tuple[str, str, str | None]]]) -> list[Any]:
        start = (table, column)
        seen = {start}
        out = []
        queue = deque([(start, 0)])
        while queue:
            node, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for t, c, ev in graph.get(node, []):
                if (t, c) in seen:
                    continue
                seen.add((t, c))
                out.append(
                    {"table": t, "column": c, "depth": depth + 1, "evolution": ev, "via": node[1]}
                )
                queue.append(((t, c), depth + 1))
        return out

    return {"table": table, "column": column, "upstreams": walk(up), "downstreams": walk(down)}


def lineage_tables(engine: Engine, table: str) -> list[str]:
    tl = db.table_lineage
    with engine.connect() as conn:
        rows = conn.execute(
            select(tl.c.source_table_full_name, tl.c.target_table_full_name).where(
                or_(tl.c.source_table_full_name == table, tl.c.target_table_full_name == table)
            )
        ).all()
    return sorted({r[0] for r in rows} | {r[1] for r in rows})
