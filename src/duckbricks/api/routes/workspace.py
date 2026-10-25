"""Workspace API subset: /api/2.0/workspace/..., plus trash and favourites for the UI."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from duckbricks.api.deps import workspace
from duckbricks.core.errors import InvalidParameter

router = APIRouter(tags=["Workspace"])


class PathBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str


class DeleteBody(PathBody):
    recursive: bool = False


class ImportBody(PathBody):
    content: str = ""
    format: str | None = None
    language: str | None = None
    overwrite: bool = False


class FavoriteBody(PathBody):
    favorite: bool = True


@router.get("/api/2.0/workspace/list")
def list_objects(path: str) -> dict[str, Any]:
    return {"objects": [o.to_json() for o in workspace().list_dir(path)]}


@router.get("/api/2.0/workspace/get-status")
def get_status(path: str) -> dict[str, Any]:
    return workspace().get_status(path).to_json()


@router.post("/api/2.0/workspace/mkdirs")
def mkdirs(body: PathBody) -> dict[str, Any]:
    workspace().mkdirs(body.path)
    return {}


@router.post("/api/2.0/workspace/import")
def import_object(body: ImportBody) -> dict[str, Any]:
    if body.format not in (None, "AUTO", "SOURCE", "RAW"):
        raise InvalidParameter(f"Import format {body.format} is not supported; use AUTO or SOURCE")
    workspace().import_base64(body.path, body.content, body.overwrite)
    return {}


@router.get("/api/2.0/workspace/export", response_model=None)
def export_object(
    path: str, format: str = "SOURCE", direct_download: bool = False
) -> dict[str, Any] | Response:
    content = workspace().export(path)
    if direct_download:
        return Response(content, media_type="application/octet-stream")
    return {"content": base64.b64encode(content).decode(), "file_type": path.rsplit(".", 1)[-1]}


@router.post("/api/2.0/workspace/delete")
def delete_object(body: DeleteBody) -> dict[str, Any]:
    workspace().delete(body.path, body.recursive)
    return {}


@router.get("/api/duckbricks/workspace/trash")
def list_trash() -> dict[str, Any]:
    return {"items": workspace().trash()}


@router.post("/api/duckbricks/workspace/trash/restore")
def restore(body: dict[str, Any]) -> dict[str, Any]:
    return {"path": workspace().restore(str(body.get("trash_id")))}


@router.post("/api/duckbricks/workspace/trash/purge")
def purge(body: dict[str, Any]) -> dict[str, Any]:
    return {"purged": workspace().purge(body.get("trash_id"))}


@router.get("/api/duckbricks/workspace/favorites")
def favorites() -> dict[str, Any]:
    ws = workspace()
    items = []
    for path in ws.favorites():
        try:
            items.append(ws.get_status(path).to_json())
        except Exception:
            continue
    return {"objects": items}


@router.post("/api/duckbricks/workspace/favorites")
def set_favorite(body: FavoriteBody) -> dict[str, Any]:
    workspace().set_favorite(body.path, body.favorite)
    return {}
