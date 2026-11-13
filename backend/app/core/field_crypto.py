"""
Symmetric encryption for sensitive fields at rest (Fernet).

Used for user SSH public keys (and similar blobs). Key material is derived
from SECRET_KEY so existing deployments do not need a new env var.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_json(payload: Any) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(token: str | None) -> Any:
    if not token:
        return None
    try:
        raw = _fernet().decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError):
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
