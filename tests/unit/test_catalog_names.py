import pytest

from duckbricks.core.catalogs import BOOTSTRAP_CATALOGS, CatalogError, validate_catalog_name


@pytest.mark.parametrize("name", ["dev", "prod", "workspace", "sales_2026", "a"])
def test_valid_names(name: str) -> None:
    validate_catalog_name(name)


@pytest.mark.parametrize("name", ["main", "system", "temp", "memory", "public", "pg_stuff"])
def test_reserved_names_are_rejected(name: str) -> None:
    with pytest.raises(CatalogError, match="reserved"):
        validate_catalog_name(name)


@pytest.mark.parametrize("name", ["Dev", "1dev", "dev-env", "", "d" * 64, "dev.x"])
def test_malformed_names_are_rejected(name: str) -> None:
    with pytest.raises(CatalogError, match="Invalid catalog name"):
        validate_catalog_name(name)


def test_bootstrap_catalogs_are_valid() -> None:
    for name in BOOTSTRAP_CATALOGS:
        validate_catalog_name(name)
