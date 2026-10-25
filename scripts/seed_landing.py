"""Write the shop demo's landing data to s3://landing/shop/ (SPEC section 12).

Deterministic: a fixed random seed gives the same customers, products and 90 days of orders on
every run, so re-seeding overwrites identical files. A few dirty rows are included on purpose
(missing customer, zero or negative quantity, unknown status, duplicated order lines).

    uv run python scripts/seed_landing.py [--days 90] [--start 2026-07-01]

Also writes web events for the pipeline example under s3://landing/shop/events/.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import tempfile
from pathlib import Path

from duckbricks.core.config import get_settings
from duckbricks.core.session import open_session, sql_literal

SEED = 20260701
STATUSES = ["completed"] * 70 + ["shipped"] * 15 + ["cancelled"] * 10 + ["returned"] * 5
COUNTRIES = ["GB", "IE", "FR", "DE", "NL", "ES", "US"]
FIRST = ["Ada", "Alan", "Grace", "Linus", "Margaret", "Ken", "Barbara", "Dennis", "Frances"]
LAST = ["Lovelace", "Turing", "Hopper", "Torvalds", "Hamilton", "Thompson", "Liskov", "Ritchie"]


def customers(rng: random.Random, count: int = 200) -> list[dict[str, str]]:
    rows = []
    for i in range(1, count + 1):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        rows.append(
            {
                "customer_id": f"C{i:04d}",
                "name": f" {first} {last} ",
                "email": f"{first}.{last}{i}@Example.com",
                "country": rng.choice(COUNTRIES).lower(),
                "signup_date": (
                    dt.date(2025, 1, 1) + dt.timedelta(days=rng.randrange(500))
                ).isoformat(),
            }
        )
    return rows


def products(rng: random.Random, count: int = 30) -> list[dict[str, str]]:
    return [
        {"product_id": f"P{i:03d}", "unit_price": f"{rng.uniform(3, 250):.2f}"}
        for i in range(1, count + 1)
    ]


def orders_for_day(
    rng: random.Random, day: dt.date, n_customers: int, catalog: list[dict[str, str]], start: int
) -> list[dict[str, str]]:
    rows = []
    for i in range(rng.randint(20, 60)):
        product = rng.choice(catalog)
        ts = dt.datetime.combine(day, dt.time()) + dt.timedelta(seconds=rng.randrange(86_400))
        rows.append(
            {
                "order_id": f"O{start + i:07d}",
                "customer_id": f"C{rng.randint(1, n_customers):04d}",
                "product_id": product["product_id"],
                "order_ts": ts.isoformat(sep=" "),
                "quantity": str(rng.randint(1, 5)),
                "unit_price": product["unit_price"],
                "status": rng.choice(STATUSES),
            }
        )
    # Dirty rows: dropped by silver.orders.
    if rng.random() < 0.3:
        rows[0] = {**rows[0], "customer_id": ""}
    if rng.random() < 0.3:
        rows[1] = {**rows[1], "quantity": rng.choice(["0", "-1"])}
    if rng.random() < 0.2:
        rows[2] = {**rows[2], "status": "UNKNOWN"}
    if rng.random() < 0.3:
        rows.append(dict(rows[3]))  # duplicated line; silver keeps one
    return rows


def events_for_day(rng: random.Random, day: dt.date, n_customers: int) -> list[dict[str, object]]:
    events = []
    for _ in range(rng.randint(50, 120)):
        ts = dt.datetime.combine(day, dt.time()) + dt.timedelta(seconds=rng.randrange(86_400))
        events.append(
            {
                "event_ts": ts.isoformat(sep=" "),
                "customer_id": f"C{rng.randint(1, n_customers):04d}",
                "event_type": rng.choice(["page_view"] * 6 + ["add_to_cart"] * 2 + ["checkout"]),
                "page": rng.choice(["/", "/search", "/product", "/cart", "/checkout"]),
                "duration_ms": rng.choice([rng.randint(50, 30_000)] * 19 + [-1]),
            }
        )
    return events


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(2026, 7, 1))
    parser.add_argument("--prefix", default=None, help="default s3://<landing bucket>/shop")
    args = parser.parse_args()
    settings = get_settings()
    prefix = (args.prefix or f"s3://{settings.landing_bucket}/shop").rstrip("/")
    rng = random.Random(SEED)
    people = customers(rng)
    catalog = products(rng)
    con = open_session("dbk.small", catalogs=[])
    uploads: list[tuple[Path, str]] = []
    with tempfile.TemporaryDirectory(prefix="dbk-seed-") as tmp:
        root = Path(tmp)
        write_csv(root / "customers.csv", people)
        uploads.append((root / "customers.csv", f"{prefix}/customers/customers.csv"))
        next_id = 1
        total = 0
        for offset in range(args.days):
            day = args.start + dt.timedelta(days=offset)
            rows = orders_for_day(rng, day, len(people), catalog, next_id)
            next_id += len(rows)
            total += len(rows)
            local = root / f"orders_{day.isoformat()}.csv"
            write_csv(local, rows)
            uploads.append((local, f"{prefix}/orders/dt={day.isoformat()}/orders.csv"))
            events = root / f"events_{day.isoformat()}.json"
            events.write_text(
                "\n".join(json.dumps(e) for e in events_for_day(rng, day, len(people))) + "\n"
            )
            uploads.append((events, f"{prefix}/events/dt={day.isoformat()}/events.json"))
        for local, target in uploads:
            reader = "read_json_auto" if local.suffix == ".json" else "read_csv"
            fmt = "(FORMAT json)" if local.suffix == ".json" else "(HEADER, DELIMITER ',')"
            options = ", all_varchar = true" if reader == "read_csv" else ""
            con.execute(
                f"COPY (SELECT * FROM {reader}({sql_literal(str(local))}{options})) "
                f"TO {sql_literal(target)} {fmt}"
            )
    con.close()
    print(
        f"seeded {prefix}: {len(people)} customers, {args.days} day(s) of orders "
        f"({total} lines) and web events"
    )


if __name__ == "__main__":
    main()
