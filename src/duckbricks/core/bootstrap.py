"""Bring the control database and bootstrap catalogs up to date. Idempotent."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from duckbricks.core.catalogs import ensure_bootstrap_catalogs
from duckbricks.core.config import REPO_ROOT, Settings, get_settings


def migrate() -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def bootstrap(settings: Settings | None = None) -> list[str]:
    """Run migrations, then create missing bootstrap catalogs. Returns catalogs created."""
    migrate()
    return ensure_bootstrap_catalogs(settings or get_settings())


if __name__ == "__main__":
    created = bootstrap()
    print(f"bootstrap complete; created catalogs: {created or 'none'}")
