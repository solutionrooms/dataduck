"""Spike A: two processes write different tables in one DuckLake catalog at the same time,
then each reads the other's table.

Pass criteria (SPEC.md section 13, Phase 0):
- both processes create their table and commit every insert while the other is writing
- the write windows overlap in time
- each process reads the other's table with the expected row count

Also reported, not required: what happens when both processes append to the SAME table
concurrently (the write-conflict case in SPEC.md section 5).
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
import traceback
from typing import Any

from duckbricks.core.session import open_session

CATALOG = "dev"
SCHEMA = "default"
INSERTS = 100
ROWS_PER_INSERT = 1_000
SHARED_TABLE = f"{CATALOG}.{SCHEMA}.spike_a_shared"


def table_for(worker: int) -> str:
    return f"{CATALOG}.{SCHEMA}.spike_a_p{worker}"


def run_worker(worker: int, barrier: Any, results: Any) -> None:
    out: dict[str, Any] = {"worker": worker}
    try:
        con = open_session("dbk.small", default_catalog=CATALOG)
        own, other = table_for(worker), table_for(1 - worker)

        barrier.wait()  # both create their tables at the same moment
        con.execute(f"CREATE TABLE {own} (worker INTEGER, batch INTEGER, id BIGINT)")
        barrier.wait()

        out["write_start"] = time.time()
        for batch in range(INSERTS):
            con.execute(
                f"INSERT INTO {own} SELECT {worker}, {batch}, range "
                f"FROM range({ROWS_PER_INSERT})"
            )
        out["write_end"] = time.time()

        barrier.wait()  # both finished writing
        out["own_rows"] = con.execute(f"SELECT count(*) FROM {own}").fetchone()[0]
        out["other_rows"] = con.execute(f"SELECT count(*) FROM {other}").fetchone()[0]

        # Same-table appends: informative only.
        barrier.wait()
        shared_errors = []
        for batch in range(INSERTS):
            try:
                con.execute(
                    f"INSERT INTO {SHARED_TABLE} SELECT {worker}, {batch}, range "
                    f"FROM range({ROWS_PER_INSERT})"
                )
            except Exception as exc:
                shared_errors.append(str(exc).splitlines()[0][:200])
        out["shared_errors"] = shared_errors
        con.close()
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["trace"] = traceback.format_exc()
        barrier.abort()
    results.put(out)


def main() -> int:
    setup = open_session("dbk.small", default_catalog=CATALOG)
    for table in (table_for(0), table_for(1), SHARED_TABLE):
        setup.execute(f"DROP TABLE IF EXISTS {table}")
    setup.execute(f"CREATE TABLE {SHARED_TABLE} (worker INTEGER, batch INTEGER, id BIGINT)")

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2, timeout=120)
    results = ctx.Queue()
    procs = [ctx.Process(target=run_worker, args=(i, barrier, results)) for i in (0, 1)]
    for p in procs:
        p.start()
    outs = sorted((results.get(timeout=300) for _ in procs), key=lambda o: o["worker"])
    for p in procs:
        p.join(timeout=60)

    expected = INSERTS * ROWS_PER_INSERT
    failures = [f"worker {o['worker']}: {o['error']}" for o in outs if "error" in o]
    if not failures:
        a, b = outs
        overlap = min(a["write_end"], b["write_end"]) - max(a["write_start"], b["write_start"])
        for o in outs:
            if o["own_rows"] != expected or o["other_rows"] != expected:
                failures.append(
                    f"worker {o['worker']} saw own={o['own_rows']} other={o['other_rows']}, "
                    f"expected {expected} each"
                )
        if overlap <= 0:
            failures.append(f"write windows did not overlap ({overlap:.3f}s)")
        shared_rows = setup.execute(f"SELECT count(*) FROM {SHARED_TABLE}").fetchone()[0]
        snapshots = setup.execute(
            f"SELECT count(*) FROM ducklake_snapshots('{CATALOG}')"
        ).fetchone()[0]
        summary = {
            "rows_per_table": expected,
            "write_seconds": [round(o["write_end"] - o["write_start"], 2) for o in outs],
            "overlap_seconds": round(overlap, 2),
            "reads": {f"p{o['worker']}": [o["own_rows"], o["other_rows"]] for o in outs},
            "same_table": {
                "rows": shared_rows,
                "expected": 2 * expected,
                "errors": [len(o["shared_errors"]) for o in outs],
                "first_error": next((e for o in outs for e in o["shared_errors"]), None),
            },
            "catalog_snapshots": snapshots,
        }
        print(json.dumps(summary, indent=2))
    else:
        for o in outs:
            if "trace" in o:
                print(o["trace"], file=sys.stderr)

    for table in (table_for(0), table_for(1), SHARED_TABLE):
        setup.execute(f"DROP TABLE IF EXISTS {table}")
    setup.close()

    if failures:
        print("SPIKE A FAILED\n- " + "\n- ".join(failures))
        return 1
    print("SPIKE A PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
