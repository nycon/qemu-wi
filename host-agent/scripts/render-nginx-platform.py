#!/usr/bin/env python3
"""Render /etc/qemu-interface/nginx-platform.conf (stdlib only, used by install.sh)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host-agent"))

from agent.nginx_platform import write_platform_config  # noqa: E402

if __name__ == "__main__":
    write_platform_config()
    print("Wrote /etc/qemu-interface/nginx-platform.conf")
