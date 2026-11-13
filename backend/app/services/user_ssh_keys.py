"""Per-user SSH public keys stored encrypted at rest."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.core.field_crypto import decrypt_json, encrypt_json
from app.models.user import User

_SSH_KEY_RE = re.compile(
    r"^(ssh-(?:rsa|ed25519|dss)|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)\s+[A-Za-z0-9+/=]+(?:\s+\S+)?$"
)
_MAX_KEYS = 20


def _fingerprint(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2:
        return ""
    try:
        blob = __import__("base64").b64decode(parts[1])
    except Exception:
        blob = public_key.strip().encode("utf-8")
    digest = hashlib.sha256(blob).digest()
    import base64

    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def normalize_public_key(raw: str) -> str:
    key = " ".join(raw.strip().split())
    if not key or not _SSH_KEY_RE.match(key):
        raise HTTPException(
            status_code=400,
            detail="Invalid SSH public key (expected ssh-ed25519 / ssh-rsa / ecdsa-… one-liner)",
        )
    if len(key) > 8192:
        raise HTTPException(status_code=400, detail="SSH public key too long")
    return key


def list_keys(user: User) -> list[dict[str, Any]]:
    data = decrypt_json(user.ssh_public_keys_enc)
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("id") or not item.get("public_key"):
            continue
        out.append(
            {
                "id": str(item["id"]),
                "name": str(item.get("name") or "SSH key"),
                "public_key": str(item["public_key"]),
                "fingerprint": str(item.get("fingerprint") or _fingerprint(str(item["public_key"]))),
                "created_at": str(item.get("created_at") or ""),
            }
        )
    return out


def public_key_strings(user: User) -> list[str]:
    return [k["public_key"] for k in list_keys(user)]


def save_keys(user: User, keys: list[dict[str, Any]]) -> None:
    user.ssh_public_keys_enc = encrypt_json(keys) if keys else None


def add_key(user: User, *, public_key: str, name: str | None = None) -> dict[str, Any]:
    key = normalize_public_key(public_key)
    keys = list_keys(user)
    if len(keys) >= _MAX_KEYS:
        raise HTTPException(status_code=400, detail=f"Maximum {_MAX_KEYS} SSH keys per user")
    fp = _fingerprint(key)
    if any(k["fingerprint"] == fp or k["public_key"] == key for k in keys):
        raise HTTPException(status_code=409, detail="This SSH key is already saved")
    entry = {
        "id": str(uuid.uuid4()),
        "name": (name or "").strip() or "SSH key",
        "public_key": key,
        "fingerprint": fp,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    keys.append(entry)
    save_keys(user, keys)
    return entry


def delete_key(user: User, key_id: str) -> None:
    keys = list_keys(user)
    new_keys = [k for k in keys if k["id"] != key_id]
    if len(new_keys) == len(keys):
        raise HTTPException(status_code=404, detail="SSH key not found")
    save_keys(user, new_keys)
