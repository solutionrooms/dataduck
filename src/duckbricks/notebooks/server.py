"""Start marimo in edit mode over the workspace volume (the `notebooks` container)."""

from __future__ import annotations

import os

from duckbricks.core.config import get_settings


def main() -> None:
    settings = get_settings()
    root = settings.workspace_root
    root.mkdir(parents=True, exist_ok=True)
    argv = [
        "marimo",
        "edit",
        "--headless",
        "--host",
        "0.0.0.0",
        "--port",
        "2718",
        "--skip-update-check",
        "--no-sandbox",
    ]
    token = settings.notebooks_token.get_secret_value()
    argv += ["--token", "--token-password", token] if token else ["--no-token"]
    os.chdir(root)
    os.execvp("marimo", [*argv, str(root)])


if __name__ == "__main__":
    main()
