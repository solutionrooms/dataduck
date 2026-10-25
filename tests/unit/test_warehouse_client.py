"""Statement parameters are inlined as literals for Quack (which takes SQL text only)."""

from __future__ import annotations

import datetime as dt
import decimal

from duckbricks.warehouses.client import inline_parameters, literal
from duckbricks.warehouses.lifecycle import validate


def test_literals() -> None:
    assert literal(None) == "NULL"
    assert literal(True) == "true"
    assert literal(42) == "42"
    assert literal(2.5) == "2.5::DOUBLE"
    assert literal(decimal.Decimal("12.30")) == "12.30::DECIMAL(38, 2)"
    assert literal(dt.date(2026, 1, 2)) == "DATE '2026-01-02'"
    assert literal(dt.datetime(2026, 1, 2, 3, 4, 5)) == "TIMESTAMP '2026-01-02 03:04:05'"
    assert literal("it's") == "'it''s'"


def test_inline_parameters_only_replaces_known_names() -> None:
    sql = "SELECT $a, $b, '$a' AS s, 'it''s $b', $unknown -- $a\n/* $b */ $a"
    assert inline_parameters(sql, {"a": 1, "b": "x"}) == (
        "SELECT 1, 'x', '$a' AS s, 'it''s $b', $unknown -- $a\n/* $b */ 1"
    )


def test_cluster_size_validation() -> None:
    values, warnings = validate({"name": "wh", "cluster_size": "Large", "enable_photon": True})
    assert values["cluster_size"] == "Large" and values["auto_stop_mins"] == 10
    assert warnings == ["Field 'enable_photon' is accepted but has no effect in Duckbricks"]
