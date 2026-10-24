import pytest

from duckbricks.core.node_types import parse_node_types

RAW = {
    "default": "dbk.medium",
    "node_types": {
        "dbk.small": {"memory_limit": "2GB", "threads": 2},
        "dbk.medium": {"memory_limit": "4GB", "threads": 4},
    },
}


def test_known_node_type_resolves_without_warning() -> None:
    node_type, warning = parse_node_types(RAW).resolve("dbk.small")
    assert (node_type.memory_limit, node_type.threads, warning) == ("2GB", 2, None)


def test_unknown_node_type_falls_back_to_default_with_warning() -> None:
    node_type, warning = parse_node_types(RAW).resolve("i3.xlarge")
    assert node_type.name == "dbk.medium"
    assert warning is not None and "i3.xlarge" in warning


def test_missing_node_type_uses_default_silently() -> None:
    node_type, warning = parse_node_types(RAW).resolve(None)
    assert node_type.name == "dbk.medium" and warning is None


def test_default_must_exist() -> None:
    with pytest.raises(ValueError, match="default node type"):
        parse_node_types({**RAW, "default": "dbk.huge"})


def test_repo_node_types_file_parses() -> None:
    from duckbricks.core.config import REPO_ROOT
    from duckbricks.core.node_types import load_node_types

    catalog = load_node_types(REPO_ROOT / "config" / "node_types.yml")
    assert set(catalog.node_types) == {"dbk.small", "dbk.medium", "dbk.large"}
