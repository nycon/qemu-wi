"""Cloud image architecture validation via the host agent."""

from __future__ import annotations

import httpx

from app.core.agent_http import AGENT_SSL_VERIFY
from app.core.config import settings
ARM_FILENAME_MARKERS = ("arm64", "aarch64", "armhf")


def reject_arm_filename(filename: str) -> None:
    """Fast reject before virt-inspector when the filename clearly indicates ARM."""
    lower = filename.lower()
    if any(marker in lower for marker in ARM_FILENAME_MARKERS):
        raise ValueError(
            "Cloud image filename indicates ARM (arm64/aarch64). "
            "On x86_64 hosts use amd64 images, e.g. "
            "https://cloud-images.ubuntu.com/releases/22.04/release/"
            "jammy-server-cloudimg-amd64.img"
        )


def inspect_cloud_image_sync(file_path: str) -> str:
    """
    Ask the host agent to inspect a cloud image on disk.
    Returns detected architecture (e.g. x86_64).
    """
    with httpx.Client(verify=AGENT_SSL_VERIFY, timeout=180) as client:
        response = client.post(
            f"{settings.HOST_AGENT_URL}/api/v1/images/inspect",
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            json={"file_path": file_path},
        )
        if response.status_code != 200:
            detail = response.text
            try:
                payload = response.json()
                detail = payload.get("detail", detail)
            except Exception:
                pass
            raise ValueError(str(detail))
        data = response.json()
        arch = data.get("architecture")
        if not arch:
            raise ValueError("Host agent did not return image architecture")
        return str(arch)


async def inspect_cloud_image_async(file_path: str) -> str:
    """Async variant for FastAPI upload handlers."""
    async with httpx.AsyncClient(verify=AGENT_SSL_VERIFY, timeout=180) as client:
        response = await client.post(
            f"{settings.HOST_AGENT_URL}/api/v1/images/inspect",
            headers={"Authorization": f"Bearer {settings.HOST_AGENT_TOKEN}"},
            json={"file_path": file_path},
        )
        if response.status_code != 200:
            detail = response.text
            try:
                payload = response.json()
                detail = payload.get("detail", detail)
            except Exception:
                pass
            raise ValueError(str(detail))
        data = response.json()
        arch = data.get("architecture")
        if not arch:
            raise ValueError("Host agent did not return image architecture")
        return str(arch)
