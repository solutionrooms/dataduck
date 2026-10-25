"""Workspace: a file tree on the workspace volume, addressed by Databricks-style paths.

`/Users/<user>/...` maps to `<workspace_root>/Users/<user>/...`. A `/Workspace` prefix is
accepted and stripped, as Databricks accepts both forms. Deleted objects move to a trash folder
and can be restored.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, insert, select

from duckbricks.core import db
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import AlreadyExists, InvalidParameter, NotFound

TRASH_DIR = ".trash"
NOTEBOOK_MARKERS = ("marimo.App(", "# Databricks notebook source", "-- Databricks notebook source")
LANGUAGES = {".py": "PYTHON", ".sql": "SQL", ".scala": "SCALA", ".r": "R"}


@dataclass(frozen=True)
class ObjectInfo:
    path: str
    object_type: str  # DIRECTORY, FILE, NOTEBOOK
    object_id: int
    created_at: int
    modified_at: int
    size: int | None = None
    language: str | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {
            "path": self.path,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }
        if self.size is not None:
            out["size"] = self.size
        if self.language is not None:
            out["language"] = self.language
        return out


def normalise(path: str) -> str:
    """Canonical workspace path: absolute, no `/Workspace` prefix, no `..`, no trailing `/`."""
    if not path or not path.startswith("/"):
        raise InvalidParameter(f"Path ({path}) must be absolute")
    pure = PurePosixPath(path)
    parts = [p for p in pure.parts[1:] if p not in ("", ".")]
    if ".." in parts:
        raise InvalidParameter(f"Path ({path}) must not contain '..'")
    if parts and parts[0] == "Workspace":
        parts = parts[1:]
    if parts and parts[0] == TRASH_DIR:
        raise InvalidParameter(f"Path ({path}) is reserved")
    return "/" + "/".join(parts)


class Workspace:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.root = self.settings.workspace_root

    # --- path mapping ------------------------------------------------------------------

    def local(self, path: str) -> Path:
        return self.root / normalise(path).lstrip("/")

    def workspace_path(self, local: Path) -> str:
        return "/" + local.relative_to(self.root).as_posix() if local != self.root else "/"

    def ensure_home(self) -> None:
        for path in (f"/Users/{self.settings.user_name}", "/Shared"):
            self.local(path).mkdir(parents=True, exist_ok=True)

    # --- reads -------------------------------------------------------------------------

    def info(self, local: Path) -> ObjectInfo:
        stat = local.stat()
        path = self.workspace_path(local)
        object_id = int(hashlib.sha1(path.encode()).hexdigest()[:13], 16)
        created = getattr(stat, "st_birthtime", None) or min(stat.st_ctime, stat.st_mtime)
        common = {
            "path": path,
            "object_id": object_id,
            "created_at": int(created * 1000),
            "modified_at": int(stat.st_mtime * 1000),
        }
        if local.is_dir():
            return ObjectInfo(object_type="DIRECTORY", **common)  # type: ignore[arg-type]
        language = LANGUAGES.get(local.suffix.lower())
        object_type = "NOTEBOOK" if language and self._is_notebook(local) else "FILE"
        return ObjectInfo(
            object_type=object_type,
            size=stat.st_size,
            language=language,
            **common,  # type: ignore[arg-type]
        )

    @staticmethod
    def _is_notebook(local: Path) -> bool:
        try:
            head = local.read_bytes()[:4096].decode("utf-8", errors="ignore")
        except OSError:
            return False
        return any(marker in head for marker in NOTEBOOK_MARKERS)

    def get_status(self, path: str) -> ObjectInfo:
        local = self.local(path)
        if not local.exists():
            raise NotFound(f"Path ({normalise(path)}) doesn't exist.")
        return self.info(local)

    def list_dir(self, path: str) -> list[ObjectInfo]:
        local = self.local(path)
        if not local.exists():
            raise NotFound(f"Path ({normalise(path)}) doesn't exist.")
        if not local.is_dir():
            return [self.info(local)]
        children = [c for c in local.iterdir() if not (local == self.root and c.name == TRASH_DIR)]
        infos = [self.info(c) for c in children]
        return sorted(infos, key=lambda i: (i.object_type != "DIRECTORY", i.path.lower()))

    def export(self, path: str) -> bytes:
        local = self.local(path)
        if not local.is_file():
            raise NotFound(f"Path ({normalise(path)}) doesn't exist or is a directory.")
        return local.read_bytes()

    # --- writes ------------------------------------------------------------------------

    def mkdirs(self, path: str) -> None:
        local = self.local(path)
        if local.exists() and not local.is_dir():
            raise AlreadyExists(f"Path ({normalise(path)}) exists and is not a directory.")
        local.mkdir(parents=True, exist_ok=True)

    def import_(self, path: str, content: bytes, overwrite: bool = False) -> ObjectInfo:
        local = self.local(path)
        if local.is_dir():
            raise AlreadyExists(f"Path ({normalise(path)}) is a directory.")
        if local.exists() and not overwrite:
            raise AlreadyExists(f"Path ({normalise(path)}) already exists.")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        return self.info(local)

    def import_base64(self, path: str, content_b64: str, overwrite: bool = False) -> ObjectInfo:
        try:
            content = base64.b64decode(content_b64, validate=True)
        except ValueError as exc:
            raise InvalidParameter(f"content is not valid base64: {exc}") from exc
        return self.import_(path, content, overwrite)

    def delete(self, path: str, recursive: bool = False) -> str:
        """Move an object to the trash. Returns its trash id."""
        canonical = normalise(path)
        local = self.local(canonical)
        if canonical == "/":
            raise InvalidParameter("Cannot delete the workspace root")
        if not local.exists():
            raise NotFound(f"Path ({canonical}) doesn't exist.")
        if local.is_dir() and any(local.iterdir()) and not recursive:
            raise InvalidParameter(
                f"Folder ({canonical}) is not empty. Set recursive to true to delete it.",
                error_code="DIRECTORY_NOT_EMPTY",
            )
        object_type = self.info(local).object_type
        trash_id = uuid.uuid4().hex
        destination = self.root / TRASH_DIR / trash_id / local.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(local, destination)
        with db.get_engine(self.settings.control_url).begin() as conn:
            conn.execute(
                insert(db.workspace_trash).values(
                    trash_id=trash_id, original_path=canonical, object_type=object_type
                )
            )
            conn.execute(
                delete(db.workspace_favorites).where(
                    db.workspace_favorites.c.path.startswith(canonical)
                )
            )
        return trash_id

    def delete_tree(self, path: str) -> None:
        """Remove a folder outright (bundle file sync and destroy), bypassing the trash."""
        local = self.local(path)
        if local.is_dir():
            shutil.rmtree(local)
        elif local.exists():
            local.unlink()

    # --- trash and favourites (Duckbricks extensions used by the UI) -------------------

    def trash(self) -> list[dict[str, object]]:
        with db.get_engine(self.settings.control_url).connect() as conn:
            rows = conn.execute(
                select(db.workspace_trash).order_by(db.workspace_trash.c.deleted_at.desc())
            ).mappings()
            return [
                {
                    "trash_id": r["trash_id"],
                    "path": r["original_path"],
                    "object_type": r["object_type"],
                    "deleted_at": int(r["deleted_at"].timestamp() * 1000),
                }
                for r in rows
            ]

    def restore(self, trash_id: str) -> str:
        engine = db.get_engine(self.settings.control_url)
        with engine.begin() as conn:
            row = (
                conn.execute(
                    select(db.workspace_trash).where(db.workspace_trash.c.trash_id == trash_id)
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound(f"Trash item {trash_id} doesn't exist.")
            target = self.local(row["original_path"])
            if target.exists():
                raise AlreadyExists(f"Path ({row['original_path']}) already exists.")
            source_dir = self.root / TRASH_DIR / trash_id
            source = next(source_dir.iterdir())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, target)
            shutil.rmtree(source_dir, ignore_errors=True)
            conn.execute(
                delete(db.workspace_trash).where(db.workspace_trash.c.trash_id == trash_id)
            )
            return str(row["original_path"])

    def purge(self, trash_id: str | None = None) -> int:
        engine = db.get_engine(self.settings.control_url)
        with engine.begin() as conn:
            query = select(db.workspace_trash.c.trash_id)
            if trash_id is not None:
                query = query.where(db.workspace_trash.c.trash_id == trash_id)
            ids = [r[0] for r in conn.execute(query)]
            for tid in ids:
                shutil.rmtree(self.root / TRASH_DIR / tid, ignore_errors=True)
            if ids:
                conn.execute(
                    delete(db.workspace_trash).where(db.workspace_trash.c.trash_id.in_(ids))
                )
            return len(ids)

    def favorites(self) -> list[str]:
        with db.get_engine(self.settings.control_url).connect() as conn:
            rows = conn.execute(
                select(db.workspace_favorites.c.path).order_by(db.workspace_favorites.c.path)
            )
            return [r[0] for r in rows]

    def set_favorite(self, path: str, favorite: bool) -> None:
        canonical = normalise(path)
        engine = db.get_engine(self.settings.control_url)
        with engine.begin() as conn:
            conn.execute(
                delete(db.workspace_favorites).where(db.workspace_favorites.c.path == canonical)
            )
            if favorite:
                self.get_status(canonical)
                conn.execute(insert(db.workspace_favorites).values(path=canonical))
