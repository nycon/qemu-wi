"""Shared VM backup operations (delete local + remote artifacts)."""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
from app.models.backup import BackupStatus, VMBackup
from app.services import backup_storage as storage_svc

AGENT_BASE_URL = settings.HOST_AGENT_URL
AGENT_HEADERS = {"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"}


def backup_is_creating(backup: VMBackup) -> bool:
    status_val = backup.status.value if hasattr(backup.status, "value") else str(backup.status)
    return status_val == BackupStatus.CREATING.value or status_val == "creating"


async def purge_vm_backup_storage(db: AsyncSession, backup: VMBackup) -> None:
    """Best-effort delete of local host-agent file and remote FTP/S3 object."""
    if backup.file_path:
        try:
            async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=30) as client:
                resp = await client.delete(
                    f"{AGENT_BASE_URL}/api/v1/backups/{backup.file_path.split('/')[-1]}",
                    headers=AGENT_HEADERS,
                )
                resp.raise_for_status()
        except Exception:
            pass

    if backup.remote_path and (backup.storage_backend or "") in ("ftp", "s3"):
        try:
            cfg = await storage_svc.get_backup_storage_config(db)
            storage_svc.delete_remote_backup(cfg, backup.remote_path)
        except Exception:
            pass
