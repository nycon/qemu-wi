"""
Storage service for disk image operations.

Handles:
- Cloning base images for new VMs
- Extracting .tar.xz archives
- Disk resize operations
- Volume creation and deletion
"""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from agent.config import agent_settings
from services.path_security import assert_safe_id, assert_safe_name, resolve_under_allowlist


class StorageService:
    """Service for managing VM disk images and storage operations."""

    def __init__(self):
        self.storage_path = Path(agent_settings.STORAGE_BASE_PATH)
        self.images_path = Path(agent_settings.CLOUD_IMAGES_PATH)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.images_path.mkdir(parents=True, exist_ok=True)

    def _architecture_from_filename(self, image_path: str) -> str | None:
        """Best-effort arch from common cloud-image filename markers."""
        name = Path(image_path).name.lower()
        if any(m in name for m in ("arm64", "aarch64")):
            return "aarch64"
        if "armhf" in name:
            return "armhf"
        if any(m in name for m in ("amd64", "x86_64", "x86-64")):
            return "x86_64"
        return None

    def get_image_architecture(self, image_path: str) -> str | None:
        """Detect guest CPU architecture (virt-inspector, then filename fallback)."""
        if shutil.which("virt-inspector"):
            env = os.environ.copy()
            # Direct backend avoids libvirt/fuse issues on some dedicated hosts.
            env.setdefault("LIBGUESTFS_BACKEND", "direct")
            try:
                result = subprocess.run(
                    ["virt-inspector", "-a", image_path],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                    env=env,
                )
                if result.returncode == 0:
                    match = re.search(
                        r"<architecture>([^<]+)</architecture>", result.stdout
                    )
                    if match:
                        return match.group(1).strip()
            except (subprocess.TimeoutExpired, OSError):
                pass

        return self._architecture_from_filename(image_path)

    def inspect_image_for_host(self, image_path: str) -> dict:
        """
        Detect guest architecture and verify it matches this KVM host.
        Raises RuntimeError when inspection or compatibility check fails.
        """
        guest_arch = self.get_image_architecture(image_path)
        if not guest_arch:
            raise RuntimeError(
                "Could not detect cloud image architecture. "
                "Use an amd64 image filename (contains amd64/x86_64), "
                "or install guestfs-tools for virt-inspector."
            )

        host_arch = platform.machine().lower()
        self._assert_image_matches_host(image_path)
        return {
            "architecture": guest_arch,
            "host_architecture": host_arch,
            "compatible": True,
        }

    def _assert_image_matches_host(self, image_path: str) -> None:
        """Reject ARM/other images on x86_64 KVM hosts with a clear error."""
        host_arch = platform.machine().lower()
        guest_arch = self.get_image_architecture(image_path)
        if not guest_arch:
            raise RuntimeError(
                f"Could not determine architecture of cloud image '{image_path}'. "
                "Use a known amd64 image (filename containing amd64/x86_64), e.g. "
                "https://cloud-images.ubuntu.com/releases/22.04/release/"
                "jammy-server-cloudimg-amd64.img"
            )

        guest_arch_l = guest_arch.lower()
        if host_arch in ("x86_64", "amd64") and guest_arch_l in (
            "aarch64",
            "arm64",
            "armhf",
            "ppc64le",
            "s390x",
            "riscv64",
        ):
            raise RuntimeError(
                f"Cloud image architecture is {guest_arch}, but this KVM host is "
                f"{host_arch}. Download an amd64 image, e.g. "
                "https://cloud-images.ubuntu.com/releases/22.04/release/"
                "jammy-server-cloudimg-amd64.img"
            )

    def clone_image(
        self,
        source_path: str,
        vm_id: str,
        disk_size_gb: int,
        disk_format: str = "qcow2",
        storage_base_path: str | None = None,
    ) -> str:
        """
        Clone a base cloud image for a new VM.
        Creates a standalone qcow2 copy and installs a legacy BIOS boot loader.

        Args:
            source_path: Path to the template/base image
            vm_id: Unique VM identifier
            disk_size_gb: Target disk size in GB
            disk_format: Disk format (qcow2 or raw)
            storage_base_path: Custom storage pool path (uses default if None)

        Returns:
            Path to the cloned disk image
        """
        assert_safe_id(vm_id, field="vm_id")
        source_path = str(resolve_under_allowlist(source_path, field="source_path"))
        base_path = Path(storage_base_path) if storage_base_path else self.storage_path
        base_path = resolve_under_allowlist(base_path, field="storage_base_path")
        base_path.mkdir(parents=True, exist_ok=True)

        vm_storage_dir = base_path / vm_id
        vm_storage_dir.mkdir(parents=True, exist_ok=True)
        resolve_under_allowlist(vm_storage_dir, field="vm_storage_dir")

        target_path = vm_storage_dir / f"disk.{disk_format}"

        self._assert_image_matches_host(source_path)
        guest_arch = self.get_image_architecture(source_path)
        if guest_arch:
            print(f"[Storage] Cloud image architecture: {guest_arch}")

        if target_path.exists():
            target_path.unlink()

        convert = subprocess.run(
            [
                "qemu-img", "convert", "-U", "-O", "qcow2",
                source_path, str(target_path),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if convert.returncode != 0:
            raise RuntimeError(f"Image clone failed: {convert.stderr}")

        resize = subprocess.run(
            ["qemu-img", "resize", str(target_path), f"{disk_size_gb}G"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if resize.returncode != 0:
            raise RuntimeError(f"Image resize failed: {resize.stderr}")

        self._ensure_libvirt_disk_access(target_path)
        self.prepare_bios_grub_boot(target_path)
        print(f"[Storage] Clone complete: {target_path}")
        return str(target_path)

    def create_volume(
        self,
        vm_id: str,
        disk_name: str,
        size_gb: int,
        disk_format: str = "qcow2",
        storage_base_path: str | None = None,
    ) -> str:
        """Create a new empty disk volume."""
        assert_safe_id(vm_id, field="vm_id")
        assert_safe_name(disk_name, field="disk_name")
        base_path = Path(storage_base_path) if storage_base_path else self.storage_path
        base_path = resolve_under_allowlist(base_path, field="storage_base_path")
        vm_storage_dir = base_path / vm_id
        vm_storage_dir.mkdir(parents=True, exist_ok=True)
        resolve_under_allowlist(vm_storage_dir, field="vm_storage_dir")

        target_path = vm_storage_dir / f"{disk_name}.{disk_format}"

        cmd = [
            "qemu-img", "create",
            "-f", disk_format,
            str(target_path),
            f"{size_gb}G",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Volume creation failed: {result.stderr}")

        return str(target_path)

    def resize_volume(self, disk_path: str, new_size_gb: int) -> None:
        """Resize an existing disk volume."""
        cmd = [
            "qemu-img", "resize",
            disk_path,
            f"{new_size_gb}G",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Volume resize failed: {result.stderr}")

    def delete_volume(self, disk_path: str) -> None:
        """Delete a disk volume."""
        path = Path(disk_path)
        if path.exists():
            path.unlink()

    def delete_vm_storage(self, vm_id: str) -> None:
        """Delete all storage for a VM."""
        vm_storage_dir = self.storage_path / vm_id
        if vm_storage_dir.exists():
            shutil.rmtree(vm_storage_dir, ignore_errors=True)

    def extract_tar_xz(self, archive_path: str, image_name: str) -> str:
        """
        Extract a .tar.xz cloud image archive.

        Returns path to the extracted qcow2 image.
        """
        import tarfile

        assert_safe_name(image_name, field="image_name")
        archive_path = str(resolve_under_allowlist(archive_path, field="archive_path"))
        extract_dir = self.images_path / image_name
        extract_dir.mkdir(parents=True, exist_ok=True)
        resolve_under_allowlist(extract_dir, field="extract_dir")

        try:
            with tarfile.open(archive_path, "r:xz") as tar:
                # Python 3.12+ data filter blocks path traversal / absolute links.
                tar.extractall(path=extract_dir, filter="data")
        except TypeError:
            # Fallback for older Python: manual member filtering.
            with tarfile.open(archive_path, "r:xz") as tar:
                for member in tar.getmembers():
                    dest = (extract_dir / member.name).resolve()
                    if not str(dest).startswith(str(extract_dir.resolve()) + os.sep) and dest != extract_dir.resolve():
                        raise RuntimeError(f"Unsafe archive member: {member.name}")
                    tar.extract(member, path=extract_dir)
        except Exception as e:
            raise RuntimeError(f"Archive extraction failed: {e}") from e

        # Find the extracted qcow2 file
        for f in extract_dir.rglob("*.qcow2"):
            return str(f)
        for f in extract_dir.rglob("*.img"):
            return str(f)
        for f in extract_dir.rglob("*.raw"):
            return str(f)

        raise RuntimeError(
            f"No disk image found in archive: {archive_path}"
        )

    def get_backing_chain(self, image_path: str) -> list[tuple[str, str]]:
        """
        Return disk paths from top (overlay) to base, each with qemu format.
        Used to embed the backing chain in libvirt XML for AppArmor/sVirt.
        """
        chain: list[tuple[str, str]] = []
        current = os.path.realpath(image_path)
        seen: set[str] = set()

        while current and current not in seen:
            seen.add(current)
            info = self.get_image_info(current)
            chain.append((current, info.get("format", "qcow2")))
            backing = info.get("backing-filename") or info.get("full-backing-filename")
            if not backing:
                break
            current = os.path.realpath(backing)

        return chain

    def prepare_bios_grub_boot(self, disk_path: str) -> None:
        """
        Ubuntu cloud images often ship UEFI-only; install i386-pc GRUB for SeaBIOS.
        Also enable tty0 on the VGA console for the web VNC viewer.
        """
        if not shutil.which("virt-customize"):
            print(
                "[Storage] virt-customize not found; install guestfs-tools for BIOS boot"
            )
            return

        grub_cfg = (
            'GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200n8"\n'
            'GRUB_TERMINAL_OUTPUT="console serial gfxterm"\n'
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".cfg", delete=False
        ) as handle:
            handle.write(grub_cfg)
            local_cfg = handle.name

        try:
            result = subprocess.run(
                [
                    "virt-customize",
                    "-a",
                    disk_path,
                    "--upload",
                    f"{local_cfg}:/etc/default/grub.d/99-qemu-interface.cfg",
                    "--run-command",
                    (
                        "set -e; "
                        "DEV=$(lsblk -ndo NAME,TYPE | awk '$2==\"disk\"{print $1; exit}'); "
                        "grub-install --target=i386-pc --boot-directory=/boot "
                        "--no-nvram-devices-only \"/dev/${DEV}\" "
                        "2>/dev/null || grub-install --target=i386-pc \"/dev/${DEV}\"; "
                        "update-grub 2>/dev/null || grub-mkconfig -o /boot/grub/grub.cfg"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            if result.returncode != 0:
                print(
                    f"[Storage] BIOS grub install failed: {result.stderr.strip()}"
                )
            else:
                print(f"[Storage] Legacy BIOS boot configured on {disk_path}")
        finally:
            Path(local_cfg).unlink(missing_ok=True)

    def build_libvirt_disk_xml(self, disk_path: str) -> str:
        """Build libvirt <disk> XML including backingStore chain when present."""
        try:
            chain = self.get_backing_chain(disk_path)
        except Exception:
            chain = [(os.path.realpath(disk_path), "qcow2")]

        top_path, top_fmt = chain[0]
        lines = [
            "    <disk type='file' device='disk'>",
            f"      <driver name='qemu' type='{top_fmt}' cache='writeback' discard='unmap'/>",
            f"      <source file='{top_path}'/>",
            "      <target dev='vda' bus='virtio'/>",
        ]

        if len(chain) > 1:
            lines.append(self._backing_store_xml(chain[1:], indent=6))

        lines.append("    </disk>")
        return "\n".join(lines)

    def _backing_store_xml(
        self, chain_tail: list[tuple[str, str]], indent: int
    ) -> str:
        pad = " " * indent
        path, fmt = chain_tail[0]
        inner = ""
        if len(chain_tail) > 1:
            inner = "\n" + self._backing_store_xml(chain_tail[1:], indent + 2)
        return (
            f"{pad}<backingStore type='file'>"
            f"\n{pad}  <format type='{fmt}'/>"
            f"\n{pad}  <source file='{path}'/>"
            f"{inner}"
            f"\n{pad}</backingStore>"
        )

    def _ensure_libvirt_disk_access(self, *paths: str) -> None:
        """Ensure libvirt-qemu can read overlay and backing files."""
        try:
            import pwd

            qemu_uid = pwd.getpwnam("libvirt-qemu").pw_uid
            kvm_gid = pwd.getpwnam("kvm").pw_gid
        except KeyError:
            return

        for raw in paths:
            path = Path(raw)
            if not path.exists():
                continue
            try:
                os.chown(path, qemu_uid, kvm_gid)
                os.chmod(path, 0o664)
            except OSError:
                pass

    def get_image_info(self, image_path: str) -> dict:
        """Get information about a disk image using qemu-img info."""
        cmd = [
            "qemu-img", "info",
            "--output=json",
            "-U",
            image_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Image info failed: {result.stderr}")

        return json.loads(result.stdout)

    def compute_checksum(self, file_path: str) -> str:
        """Compute SHA256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
