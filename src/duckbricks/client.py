"""HTTP client for the Duckbricks API, shared by the CLI, the MCP server and tests.

Configuration: `DBK_HOST` (default http://127.0.0.1:8080) and `DBK_TOKEN`, or a profile in
`~/.dbk/config` (INI, like ~/.databrickscfg: `[DEFAULT] host = ... token = ...`).
"""

from __future__ import annotations

import configparser
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_HOST = "http://127.0.0.1:8080"
TERMINAL = ("TERMINATED", "SKIPPED", "INTERNAL_ERROR")


class ApiCallError(Exception):
    def __init__(self, status: int, error_code: str, message: str):
        super().__init__(f"{error_code}: {message}")
        self.status = status
        self.error_code = error_code
        self.message = message


def _profile(name: str) -> dict[str, str]:
    path = Path(os.environ.get("DBK_CONFIG_FILE", Path.home() / ".dbk" / "config"))
    if not path.exists():
        return {}
    parser = configparser.ConfigParser()
    parser.read(path)
    if name == "DEFAULT":
        return dict(parser.defaults())
    return dict(parser[name]) if parser.has_section(name) else {}


def _dotenv_token() -> str | None:
    """Fall back to the repo's .env so `dbk` works straight after `make up`."""
    for directory in (Path.cwd(), *Path.cwd().parents):
        env = directory / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("DBK_TOKEN="):
                    return line.split("=", 1)[1].strip()
            return None
    return None


class DuckbricksClient:
    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        profile: str | None = None,
        user_agent: str = "dbk-cli",
        timeout: float = 120.0,
    ):
        prof = _profile(profile or os.environ.get("DBK_PROFILE", "DEFAULT"))
        self.host = (host or os.environ.get("DBK_HOST") or prof.get("host") or DEFAULT_HOST).rstrip(
            "/"
        )
        self.token = token or os.environ.get("DBK_TOKEN") or prof.get("token") or _dotenv_token()
        headers = {"User-Agent": user_agent}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self.http = httpx.Client(base_url=self.host, headers=headers, timeout=timeout)

    def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise ApiCallError(
                0,
                "UNAVAILABLE",
                f"Cannot reach the Duckbricks API at {self.host}. Is the stack up (`make up`)?",
            ) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"error_code": "HTTP_ERROR", "message": response.text[:500]}
            raise ApiCallError(
                response.status_code,
                str(body.get("error_code", "HTTP_ERROR")),
                str(body.get("message", body)),
            )
        if not response.content:
            return {}
        result: dict[str, Any] = response.json()
        return result

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        return self.request("GET", path, params=clean)

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("POST", path, json=body or {})

    def delete(self, path: str, **params: Any) -> dict[str, Any]:
        return self.request(
            "DELETE", path, params={k: v for k, v in params.items() if v is not None}
        )

    # --- conveniences ----------------------------------------------------------------------

    def sql(
        self,
        statement: str,
        catalog: str | None = None,
        schema: str | None = None,
        warehouse_id: str | None = None,
        read_only: bool = False,
        row_limit: int | None = None,
        wait_seconds: float = 300,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "statement": statement,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
            "read_only": read_only,
        }
        for key, value in (
            ("catalog", catalog),
            ("schema", schema),
            ("warehouse_id", warehouse_id),
            ("row_limit", row_limit),
        ):
            if value is not None:
                body[key] = value
        result = self.post("/api/2.0/sql/statements", body)
        deadline = time.monotonic() + wait_seconds
        while result["status"]["state"] in ("PENDING", "RUNNING"):
            if time.monotonic() > deadline:
                self.post(f"/api/2.0/sql/statements/{result['statement_id']}/cancel")
                raise ApiCallError(0, "TIMEOUT", f"Statement still running after {wait_seconds}s")
            time.sleep(0.5)
            result = self.get(f"/api/2.0/sql/statements/{result['statement_id']}")
        return result

    def rows(self, statement: str, **kwargs: Any) -> list[dict[str, Any]]:
        result = self.sql(statement, **kwargs)
        if result["status"]["state"] != "SUCCEEDED":
            error = result["status"].get("error", {})
            raise ApiCallError(400, error.get("error_code", "FAILED"), error.get("message", ""))
        columns = [
            c["name"] for c in result.get("manifest", {}).get("schema", {}).get("columns", [])
        ]
        return [
            dict(zip(columns, row, strict=True))
            for row in result.get("result", {}).get("data_array", [])
        ]

    def wait_for_run(self, run_id: int, timeout: float = 1800, poll: float = 1.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            run = self.get("/api/2.2/jobs/runs/get", run_id=run_id)
            if run["state"]["life_cycle_state"] in TERMINAL:
                return run
            if time.monotonic() > deadline:
                raise ApiCallError(0, "TIMEOUT", f"Run {run_id} still running after {timeout}s")
            time.sleep(poll)
