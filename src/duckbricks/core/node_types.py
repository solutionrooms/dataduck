"""Node types: the memory and thread limits a DuckDB process runs with."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from duckbricks.core.config import get_settings


@dataclass(frozen=True)
class NodeType:
    name: str
    memory_limit: str
    threads: int


@dataclass(frozen=True)
class NodeTypeCatalog:
    default: str
    node_types: dict[str, NodeType]

    def resolve(self, node_type_id: str | None) -> tuple[NodeType, str | None]:
        """Map a node_type_id to a node type, with a warning when it is unknown."""
        if node_type_id is None:
            return self.node_types[self.default], None
        if node_type_id in self.node_types:
            return self.node_types[node_type_id], None
        warning = (
            f"node_type_id '{node_type_id}' is not a Duckbricks node type; using {self.default}"
        )
        return self.node_types[self.default], warning


def parse_node_types(raw: dict[str, object]) -> NodeTypeCatalog:
    entries = raw.get("node_types")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("node_types.yml must define at least one entry under 'node_types'")
    node_types = {
        name: NodeType(
            name=name, memory_limit=str(spec["memory_limit"]), threads=int(spec["threads"])
        )
        for name, spec in entries.items()
    }
    default = str(raw.get("default", next(iter(node_types))))
    if default not in node_types:
        raise ValueError(f"default node type '{default}' is not defined")
    return NodeTypeCatalog(default=default, node_types=node_types)


def load_node_types(path: Path) -> NodeTypeCatalog:
    return parse_node_types(yaml.safe_load(path.read_text()))


@lru_cache
def get_node_types() -> NodeTypeCatalog:
    return load_node_types(get_settings().node_types_file)
