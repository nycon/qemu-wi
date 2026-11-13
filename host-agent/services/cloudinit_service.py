"""
Cloud-Init seed image creation service.

Creates NoCloud datasource images containing:
- user-data
- meta-data
- network-config (v2)
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from agent.config import agent_settings


class CloudInitService:
    """Service for creating Cloud-Init seed images."""

    def __init__(self):
        self.base_path = Path(agent_settings.CLOUD_INIT_PATH)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def create_seed_image(
        self,
        vm_id: str,
        userdata: dict,
        metadata: dict,
        network_config: dict | None = None,
    ) -> str:
        """
        Create a Cloud-Init NoCloud seed image (ISO or qcow2).

        Args:
            vm_id: Unique VM identifier
            userdata: Cloud-Init user-data configuration
            metadata: Cloud-Init meta-data
            network_config: Cloud-Init network-config (v2)

        Returns:
            Path to the created seed image
        """
        seed_dir = self.base_path / vm_id
        seed_dir.mkdir(parents=True, exist_ok=True)

        # Write user-data
        userdata_path = seed_dir / "user-data"
        userdata_content = "#cloud-config\n" + yaml.dump(
            userdata, default_flow_style=False, allow_unicode=True
        )
        userdata_path.write_text(userdata_content)

        # Write meta-data
        metadata_path = seed_dir / "meta-data"
        metadata_content = yaml.dump(
            metadata, default_flow_style=False, allow_unicode=True
        )
        metadata_path.write_text(metadata_content)

        # Write network-config (optional)
        network_path = None
        if network_config:
            network_path = seed_dir / "network-config"
            network_content = yaml.dump(
                network_config, default_flow_style=False, allow_unicode=True
            )
            network_path.write_text(network_content)

        # NoCloud on libvirt CD-ROM needs ISO9660 volume label "cidata".
        # cloud-localds defaults to a raw VFAT disk image which does not work
        # reliably as a SATA CD-ROM — prefer genisoimage.
        seed_image_path = seed_dir / "seed.img"
        if shutil.which("genisoimage") or shutil.which("mkisofs"):
            return self._create_seed_iso_fallback(
                seed_dir, seed_image_path, userdata_path, metadata_path, network_path
            )

        cmd = [
            "cloud-localds",
            str(seed_image_path),
            str(userdata_path),
            str(metadata_path),
        ]
        if network_path:
            cmd.extend(["--network-config", str(network_path)])

        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "Neither genisoimage nor cloud-localds available. "
                "Install: apt install genisoimage cloud-image-utils"
            ) from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"cloud-localds failed: {e.stderr}")

        return str(seed_image_path)

    def _create_seed_iso_fallback(
        self,
        seed_dir: Path,
        output_path: Path,
        userdata_path: Path,
        metadata_path: Path,
        network_path: Path | None,
    ) -> str:
        """
        Fallback: Create Cloud-Init seed ISO using genisoimage/mkisofs.
        """
        # Prepare a temp directory with the required structure
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Copy files to temp dir
            (tmp / "user-data").write_text(userdata_path.read_text())
            (tmp / "meta-data").write_text(metadata_path.read_text())
            if network_path and network_path.exists():
                (tmp / "network-config").write_text(network_path.read_text())

            # Try genisoimage first, then mkisofs
            for tool in ["genisoimage", "mkisofs"]:
                cmd = [
                    tool,
                    "-output", str(output_path),
                    "-volid", "cidata",
                    "-joliet",
                    "-rock",
                    str(tmp),
                ]
                try:
                    subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=True,
                    )
                    return str(output_path)
                except FileNotFoundError:
                    continue
                except subprocess.CalledProcessError as e:
                    raise RuntimeError(f"{tool} failed: {e.stderr}")

            raise RuntimeError(
                "Neither cloud-localds nor genisoimage/mkisofs available. "
                "Please install: apt install cloud-image-utils or genisoimage"
            )

    def cleanup_seed(self, vm_id: str) -> None:
        """Remove cloud-init seed files for a VM."""
        seed_dir = self.base_path / vm_id
        if seed_dir.exists():
            shutil.rmtree(seed_dir, ignore_errors=True)
