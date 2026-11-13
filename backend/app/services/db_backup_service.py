"""
PostgreSQL database dump / restore helpers for Superadmin backups.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, unquote

from app.core.config import settings

DB_BACKUP_DIR = Path("/var/lib/qemu-interface/storage/db-backups")


def _sync_database_url() -> str:
    """Convert async SQLAlchemy URL to libpq URL for pg_dump/pg_restore."""
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    return url


def parse_db_url() -> dict[str, str]:
    parsed = urlparse(_sync_database_url())
    return {
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or "qemu"),
        "password": unquote(parsed.password or ""),
        "dbname": (parsed.path or "/qemu_interface").lstrip("/") or "qemu_interface",
    }


def ensure_backup_dir() -> Path:
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return DB_BACKUP_DIR


def _env_with_password(password: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    return env


def create_database_dump(backup_id: str, name: str) -> tuple[str, int]:
    """
    Create a custom-format pg_dump file.
    Returns (file_path, size_bytes).
    """
    ensure_backup_dir()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-")[:60] or "db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{backup_id}_{safe}_{stamp}.dump"
    dest = DB_BACKUP_DIR / filename

    db = parse_db_url()
    cmd = [
        "pg_dump",
        "-Fc",
        "--no-owner",
        "--no-acl",
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "-f", str(dest),
    ]
    result = subprocess.run(
        cmd,
        env=_env_with_password(db["password"]),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "pg_dump failed")

    size = dest.stat().st_size
    return str(dest), size


def list_dump_toc(file_path: str) -> str:
    """Validate dump file via pg_restore -l (does not restore)."""
    db = parse_db_url()
    result = subprocess.run(
        ["pg_restore", "-l", file_path],
        env=_env_with_password(db["password"]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Invalid dump file")
    return result.stdout


def restore_database_dump(file_path: str) -> None:
    """
    Restore dump into the configured database.
    Uses --clean --if-exists. App connections may briefly fail.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Dump not found: {file_path}")

    db = parse_db_url()
    cmd = [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-acl",
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        file_path,
    ]
    result = subprocess.run(
        cmd,
        env=_env_with_password(db["password"]),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    # pg_restore returns non-zero for some benign warnings; treat hard errors only
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        # Ignore "errors ignored on restore" style soft failures if DB is usable
        if "ERROR:" in err and "fatal" in err.lower():
            raise RuntimeError(err[:2000])
        if result.returncode > 1:
            raise RuntimeError(err[:2000] or "pg_restore failed")


def delete_dump_file(file_path: str | None) -> None:
    if not file_path:
        return
    try:
        Path(file_path).unlink(missing_ok=True)
    except OSError:
        pass
