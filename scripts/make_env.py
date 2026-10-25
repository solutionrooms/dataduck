"""Write .env from .env.example, replacing placeholder secrets with random values.

If .env already exists, append any settings it is missing (new secrets are random).
"""

import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def git_email() -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "user.email"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return out.stdout.strip() or None


def filled(template: str) -> str:
    replacements = {
        "DBK_PG_PASSWORD=change-me": f"DBK_PG_PASSWORD={secrets.token_urlsafe(18)}",
        "DBK_S3_SECRET_KEY=change-me-too": f"DBK_S3_SECRET_KEY={secrets.token_urlsafe(24)}",
        "DBK_TOKEN=change-me-token": f"DBK_TOKEN=dbk_{secrets.token_urlsafe(24)}",
        "DBK_WAREHOUSE_TOKEN=change-me-warehouse": (
            f"DBK_WAREHOUSE_TOKEN={secrets.token_urlsafe(24)}"
        ),
        "DBK_NOTEBOOKS_TOKEN=change-me-notebooks": (
            f"DBK_NOTEBOOKS_TOKEN={secrets.token_urlsafe(18)}"
        ),
    }
    email = git_email()
    if email:
        replacements["DBK_USER_NAME=user@duckbricks.local"] = f"DBK_USER_NAME={email}"
    lines = []
    for line in template.splitlines():
        lines.append(replacements.get(line, line))
    return "\n".join(lines) + "\n"


def main() -> None:
    template = filled((root / ".env.example").read_text())
    target = root / ".env"
    if not target.exists():
        target.write_text(template)
        target.chmod(0o600)
        print("wrote .env")
        return
    existing = target.read_text()
    have = {line.split("=", 1)[0] for line in existing.splitlines() if "=" in line}
    missing = [
        line
        for line in template.splitlines()
        if "=" in line and not line.startswith("#") and line.split("=", 1)[0] not in have
    ]
    if missing:
        target.write_text(existing.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
        print(f"added to .env: {', '.join(m.split('=', 1)[0] for m in missing)}")


if __name__ == "__main__":
    main()
