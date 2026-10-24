"""Single-user bearer token auth (Databricks-style `Authorization: Bearer <token>`)."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from duckbricks.core.config import get_settings


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().token.get_secret_value()
    if not expected:
        return
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHENTICATED", "message": "Missing or invalid token"},
        )
