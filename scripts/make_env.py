"""Write .env from .env.example, replacing placeholder secrets with random values."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = (root / ".env.example").read_text()
src = src.replace("DBK_PG_PASSWORD=change-me\n", f"DBK_PG_PASSWORD={secrets.token_urlsafe(18)}\n")
src = src.replace(
    "DBK_S3_SECRET_KEY=change-me-too", f"DBK_S3_SECRET_KEY={secrets.token_urlsafe(24)}"
)
src = src.replace("DBK_TOKEN=change-me-token", f"DBK_TOKEN=dbk_{secrets.token_urlsafe(24)}")
target = root / ".env"
target.write_text(src)
target.chmod(0o600)
print("wrote .env")
