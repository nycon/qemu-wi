"""
Strip guest credentials from Cloud-Init userdata before persisting to the DB.

Passwords are still sent to the host-agent for seed generation / live reset,
but must not remain in virtual_machines.cloud_init_userdata JSONB.
"""

from __future__ import annotations

import copy
from typing import Any


_PASSWORD_USER_KEYS = (
    "plain_text_passwd",
    "passwd",
    "password",
    "hashed_passwd",
)


def sanitize_cloud_init_userdata(userdata: dict | None) -> dict | None:
    """Return a deep copy of userdata with guest password material removed."""
    if not isinstance(userdata, dict):
        return userdata

    data: dict[str, Any] = copy.deepcopy(userdata)

    users = data.get("users")
    if isinstance(users, list):
        for entry in users:
            if not isinstance(entry, dict):
                continue
            for key in _PASSWORD_USER_KEYS:
                entry.pop(key, None)

    chpasswd = data.get("chpasswd")
    if isinstance(chpasswd, dict):
        chpasswd.pop("list", None)
        chpasswd.pop("users", None)

    return data


def cloud_init_userdata_needs_sanitize(userdata: dict | None) -> bool:
    """True if userdata still contains plaintext / hashed guest passwords."""
    if not isinstance(userdata, dict):
        return False

    users = userdata.get("users")
    if isinstance(users, list):
        for entry in users:
            if isinstance(entry, dict) and any(k in entry for k in _PASSWORD_USER_KEYS):
                return True

    chpasswd = userdata.get("chpasswd")
    if isinstance(chpasswd, dict) and (chpasswd.get("list") or chpasswd.get("users")):
        return True

    return False
