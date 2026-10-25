import datetime as dt
import decimal

import pytest

from duckbricks.core.errors import InvalidParameter
from duckbricks.core.statements import (
    convert_parameters,
    parse_wait_timeout,
    to_json_value,
    type_name,
)


def test_parameter_markers_and_casts() -> None:
    sql, values = convert_parameters(
        "select :a::int, ':b', x::varchar from t where d = :d",
        [
            {"name": "a", "value": "3", "type": "INT"},
            {"name": "d", "value": "2026-01-02", "type": "DATE"},
        ],
    )
    assert sql == "select $a::int, ':b', x::varchar from t where d = $d"
    assert values == {"a": 3, "d": dt.date(2026, 1, 2)}


def test_json_values_are_strings() -> None:
    assert to_json_value(True) == "true"
    assert to_json_value(None) is None
    assert to_json_value(decimal.Decimal("1.50")) == "1.50"
    assert to_json_value([1, 2]) == "[1, 2]"
    assert to_json_value(dt.date(2026, 1, 2)) == "2026-01-02"


def test_type_names() -> None:
    assert type_name("VARCHAR") == "STRING"
    assert type_name("DECIMAL(18,2)") == "DECIMAL"
    assert type_name("INTEGER[]") == "ARRAY"
    assert type_name("TIMESTAMP WITH TIME ZONE") == "TIMESTAMP"


def test_wait_timeout() -> None:
    assert parse_wait_timeout(None) == 10.0
    assert parse_wait_timeout("0s") == 0.0
    with pytest.raises(InvalidParameter):
        parse_wait_timeout("3s")
