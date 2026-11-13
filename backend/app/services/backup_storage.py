"""
VM backup remote storage backends: internal (local), FTP, S3/MinIO.
"""

from __future__ import annotations

import ftplib
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.platform import PlatformSetting

SETTING_KEY = "vm_backup_storage"

DEFAULT_CONFIG: dict[str, Any] = {
    "backend": "internal",
    "keep_local_copy": True,
    "ftp": {
        "host": "",
        "port": 21,
        "username": "",
        "password": "",
        "path": "/vm-backups",
        "use_tls": False,
    },
    "s3": {
        "endpoint_url": "",
        "region": "eu-central-1",
        "bucket": "",
        "access_key": "",
        "secret_key": "",
        "prefix": "vm-backups/",
    },
}


def _merge_defaults(raw: dict | None) -> dict[str, Any]:
    cfg = {
        "backend": DEFAULT_CONFIG["backend"],
        "keep_local_copy": DEFAULT_CONFIG["keep_local_copy"],
        "ftp": dict(DEFAULT_CONFIG["ftp"]),
        "s3": dict(DEFAULT_CONFIG["s3"]),
    }
    if not raw:
        return cfg
    cfg["backend"] = str(raw.get("backend") or "internal").lower()
    if cfg["backend"] not in ("internal", "ftp", "s3"):
        cfg["backend"] = "internal"
    cfg["keep_local_copy"] = bool(raw.get("keep_local_copy", True))
    if isinstance(raw.get("ftp"), dict):
        cfg["ftp"].update({k: raw["ftp"][k] for k in cfg["ftp"] if k in raw["ftp"]})
    if isinstance(raw.get("s3"), dict):
        cfg["s3"].update({k: raw["s3"][k] for k in cfg["s3"] if k in raw["s3"]})
    return cfg


def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return config with secrets redacted for API responses."""
    out = _merge_defaults(cfg)
    ftp_pw = out["ftp"].get("password") or ""
    s3_secret = out["s3"].get("secret_key") or ""
    out["ftp"]["password"] = "********" if ftp_pw else ""
    out["ftp"]["password_set"] = bool(ftp_pw)
    out["s3"]["secret_key"] = "********" if s3_secret else ""
    out["s3"]["secret_key_set"] = bool(s3_secret)
    return out


async def get_backup_storage_config(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(select(PlatformSetting).where(PlatformSetting.key == SETTING_KEY))
    ).scalar_one_or_none()
    return _merge_defaults(row.value_json if row else None)


def get_backup_storage_config_sync(session: Session) -> dict[str, Any]:
    row = session.execute(
        select(PlatformSetting).where(PlatformSetting.key == SETTING_KEY)
    ).scalar_one_or_none()
    return _merge_defaults(row.value_json if row else None)


async def save_backup_storage_config(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    current = await get_backup_storage_config(db)
    merged = _merge_defaults(payload)

    # Preserve secrets when client sends masked placeholders / empty
    ftp_pw = (payload.get("ftp") or {}).get("password")
    if not ftp_pw or ftp_pw == "********":
        merged["ftp"]["password"] = current["ftp"].get("password", "")
    s3_secret = (payload.get("s3") or {}).get("secret_key")
    if not s3_secret or s3_secret == "********":
        merged["s3"]["secret_key"] = current["s3"].get("secret_key", "")

    row = (
        await db.execute(select(PlatformSetting).where(PlatformSetting.key == SETTING_KEY))
    ).scalar_one_or_none()
    if row:
        row.value_json = merged
        db.add(row)
    else:
        db.add(PlatformSetting(key=SETTING_KEY, value_json=merged))
    await db.flush()
    return merged


def _ftp_connect(cfg: dict[str, Any]) -> ftplib.FTP:
    host = cfg["ftp"]["host"]
    port = int(cfg["ftp"].get("port") or 21)
    user = cfg["ftp"].get("username") or ""
    password = cfg["ftp"].get("password") or ""
    use_tls = bool(cfg["ftp"].get("use_tls"))
    if not host:
        raise ValueError("FTP host is required")
    if use_tls:
        ftp: ftplib.FTP = ftplib.FTP_TLS()
        ftp.connect(host, port, timeout=60)
        ftp.auth()
        try:
            ftp.prot_p()
        except Exception:
            pass
    else:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=60)
    ftp.login(user, password)
    return ftp


def _ftp_ensure_path(ftp: ftplib.FTP, path: str) -> None:
    parts = [p for p in path.strip("/").split("/") if p]
    # Try absolute then relative
    try:
        ftp.cwd("/" + "/".join(parts) if parts else "/")
        return
    except Exception:
        pass
    ftp.cwd("/")
    for part in parts:
        try:
            ftp.cwd(part)
        except Exception:
            ftp.mkd(part)
            ftp.cwd(part)


def _s3_client(cfg: dict[str, Any]):
    try:
        import boto3
        from botocore.client import Config
    except ImportError as e:
        raise RuntimeError("boto3 is required for S3 backup storage") from e

    s3 = cfg["s3"]
    kwargs: dict[str, Any] = {
        "aws_access_key_id": s3.get("access_key") or None,
        "aws_secret_access_key": s3.get("secret_key") or None,
        "region_name": s3.get("region") or "eu-central-1",
    }
    endpoint = (s3.get("endpoint_url") or "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["config"] = Config(s3={"addressing_style": "path"})
    return boto3.client("s3", **kwargs)


def upload_backup_file(cfg: dict[str, Any], local_path: str, remote_name: str) -> str:
    """Upload local backup file. Returns remote path/key."""
    backend = cfg.get("backend", "internal")
    if backend == "internal":
        return local_path

    if backend == "ftp":
        remote_dir = cfg["ftp"].get("path") or "/vm-backups"
        ftp = _ftp_connect(cfg)
        try:
            _ftp_ensure_path(ftp, remote_dir)
            with open(local_path, "rb") as fh:
                ftp.storbinary(f"STOR {remote_name}", fh)
            return f"{remote_dir.rstrip('/')}/{remote_name}"
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    if backend == "s3":
        bucket = cfg["s3"].get("bucket")
        if not bucket:
            raise ValueError("S3 bucket is required")
        prefix = (cfg["s3"].get("prefix") or "").lstrip("/")
        key = f"{prefix}{remote_name}" if prefix.endswith("/") or not prefix else f"{prefix}/{remote_name}"
        if prefix and not prefix.endswith("/") and "/" not in remote_name:
            key = f"{prefix.rstrip('/')}/{remote_name}"
        client = _s3_client(cfg)
        client.upload_file(local_path, bucket, key)
        return key

    raise ValueError(f"Unknown backup backend: {backend}")


def download_backup_file(cfg: dict[str, Any], remote_path: str, dest_path: str) -> str:
    """Download remote backup to dest_path. Returns dest_path."""
    backend = cfg.get("backend", "internal")
    if backend == "internal":
        if not os.path.isfile(remote_path):
            raise FileNotFoundError(f"Local backup not found: {remote_path}")
        if remote_path != dest_path:
            import shutil
            shutil.copy2(remote_path, dest_path)
        return dest_path

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    if backend == "ftp":
        remote_dir = os.path.dirname(remote_path) or (cfg["ftp"].get("path") or "/")
        remote_name = os.path.basename(remote_path)
        ftp = _ftp_connect(cfg)
        try:
            try:
                ftp.cwd(remote_dir)
            except Exception:
                _ftp_ensure_path(ftp, remote_dir)
            with open(dest_path, "wb") as fh:
                ftp.retrbinary(f"RETR {remote_name}", fh.write)
            return dest_path
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    if backend == "s3":
        bucket = cfg["s3"].get("bucket")
        if not bucket:
            raise ValueError("S3 bucket is required")
        client = _s3_client(cfg)
        client.download_file(bucket, remote_path, dest_path)
        return dest_path

    raise ValueError(f"Unknown backup backend: {backend}")


def delete_remote_backup(cfg: dict[str, Any], remote_path: str | None) -> None:
    if not remote_path:
        return
    backend = cfg.get("backend", "internal")
    if backend == "internal":
        return
    if backend == "ftp":
        remote_dir = os.path.dirname(remote_path) or (cfg["ftp"].get("path") or "/")
        remote_name = os.path.basename(remote_path)
        ftp = _ftp_connect(cfg)
        try:
            try:
                ftp.cwd(remote_dir)
            except Exception:
                pass
            ftp.delete(remote_name)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
        return
    if backend == "s3":
        bucket = cfg["s3"].get("bucket")
        if not bucket:
            return
        client = _s3_client(cfg)
        client.delete_object(Bucket=bucket, Key=remote_path)


def test_backup_storage(cfg: dict[str, Any]) -> dict[str, Any]:
    """Connectivity check for configured backend."""
    backend = cfg.get("backend", "internal")
    if backend == "internal":
        path = "/var/lib/qemu-interface/storage/backups"
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".qi-write-test")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return {"ok": True, "backend": "internal", "message": f"Writable: {path}"}

    if backend == "ftp":
        ftp = _ftp_connect(cfg)
        try:
            remote_dir = cfg["ftp"].get("path") or "/vm-backups"
            _ftp_ensure_path(ftp, remote_dir)
            return {"ok": True, "backend": "ftp", "message": f"Connected to {cfg['ftp']['host']}:{cfg['ftp']['port']}"}
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    if backend == "s3":
        bucket = cfg["s3"].get("bucket")
        if not bucket:
            raise ValueError("S3 bucket is required")
        client = _s3_client(cfg)
        client.head_bucket(Bucket=bucket)
        return {"ok": True, "backend": "s3", "message": f"Bucket reachable: {bucket}"}

    raise ValueError(f"Unknown backup backend: {backend}")
