"""Databricks-style API errors: `{"error_code": ..., "message": ...}` with an HTTP status."""

from __future__ import annotations


class ApiError(Exception):
    status_code = 400
    error_code = "INVALID_PARAMETER_VALUE"

    def __init__(self, message: str, error_code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code

    def body(self) -> dict[str, str]:
        return {"error_code": self.error_code, "message": self.message}


class InvalidParameter(ApiError):
    pass


class NotFound(ApiError):
    status_code = 404
    error_code = "RESOURCE_DOES_NOT_EXIST"


class AlreadyExists(ApiError):
    status_code = 409
    error_code = "RESOURCE_ALREADY_EXISTS"


class InvalidState(ApiError):
    status_code = 400
    error_code = "INVALID_STATE"
