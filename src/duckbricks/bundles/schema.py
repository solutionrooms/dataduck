"""The Databricks bundle JSON schema (vendored from `databricks bundle schema`).

Its patterns use Unicode property classes (`\\p{L}`, `\\p{N}`) that Python's `re` lacks, so they
are rewritten to equivalent classes before the validator is built.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "bundle_config_schema.json"
PACKAGED_SCHEMA = Path(__file__).resolve().parent / "bundle_config_schema.json"


def _rewrite(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                value = value.replace("\\p{L}", "[^\\W\\d_]").replace("\\p{N}", "\\d")
            out[key] = _rewrite(value)
        return out
    if isinstance(node, list):
        return [_rewrite(v) for v in node]
    return node


@lru_cache
def validator() -> Draft202012Validator:
    path = SCHEMA_PATH if SCHEMA_PATH.exists() else PACKAGED_SCHEMA
    schema = _rewrite(json.loads(path.read_text()))
    return Draft202012Validator(schema)


def schema_errors(document: dict[str, Any], limit: int = 20) -> list[str]:
    """Human-readable schema violations, most specific first."""
    errors = []
    for error in sorted(validator().iter_errors(document), key=lambda e: list(e.absolute_path)):
        location = ".".join(str(p) for p in error.absolute_path) or "(root)"
        message = error.message
        if error.validator == "additionalProperties":
            message = message.replace("Additional properties are not allowed", "unknown field")
        if error.validator in ("oneOf", "anyOf") and error.context:
            best = min(error.context, key=lambda e: len(list(e.absolute_path)) * -1)
            message = best.message
        errors.append(f"{location}: {message[:300]}")
        if len(errors) >= limit:
            break
    return errors
