"""The warehouse agent (the `warehouse` container): starts and stops warehouse processes.

A small HTTP service on port 9400, called only by the API. Each running warehouse is one
`duckbricks.warehouses.server` subprocess serving Quack on its own port.
"""

from __future__ import annotations

import secrets
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from duckbricks.core.config import get_settings

app = FastAPI(title="Duckbricks warehouse agent")
_processes: dict[str, tuple[int, subprocess.Popen[bytes]]] = {}
_lock = threading.Lock()


def auth(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().warehouse_token.get_secret_value()
    supplied = (authorization or "").removeprefix("Bearer ")
    if expected and not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid warehouse agent token")


class StartBody(BaseModel):
    port: int
    node_type: str | None = None


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/warehouses", dependencies=[Depends(auth)])
def running() -> dict[str, Any]:
    with _lock:
        alive = {
            wid: {"port": port, "pid": proc.pid}
            for wid, (port, proc) in _processes.items()
            if proc.poll() is None
        }
    return {"warehouses": alive}


@app.post("/warehouses/{warehouse_id}/start", dependencies=[Depends(auth)])
def start(warehouse_id: str, body: StartBody) -> dict[str, Any]:
    with _lock:
        current = _processes.get(warehouse_id)
        if current and current[1].poll() is None:
            return {"port": current[0], "pid": current[1].pid, "already_running": True}
        argv = [
            sys.executable,
            "-m",
            "duckbricks.warehouses.server",
            "--id",
            warehouse_id,
            "--port",
            str(body.port),
        ]
        if body.node_type:
            argv += ["--node-type", body.node_type]
        proc = subprocess.Popen(argv)
        _processes[warehouse_id] = (body.port, proc)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HTTPException(500, f"warehouse process exited with code {proc.returncode}")
        if _listening(body.port):
            return {"port": body.port, "pid": proc.pid}
        time.sleep(0.3)
    proc.kill()
    raise HTTPException(500, "warehouse did not start listening within 60s")


@app.post("/warehouses/{warehouse_id}/stop", dependencies=[Depends(auth)])
def stop(warehouse_id: str) -> dict[str, Any]:
    with _lock:
        current = _processes.pop(warehouse_id, None)
    if current is None:
        return {"stopped": False}
    _, proc = current
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    return {"stopped": True}


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=9400, log_level="warning")


if __name__ == "__main__":
    main()
