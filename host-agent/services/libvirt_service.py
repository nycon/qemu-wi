"""
Libvirt service for VM lifecycle management.

Handles domain creation, start/stop/reboot, and status queries
using the libvirt Python API.
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import libvirt

from agent.config import agent_settings


class LibvirtService:
    """Service for interacting with libvirt/QEMU."""

    def __init__(self):
        self._conn: Optional[libvirt.virConnect] = None

    @property
    def conn(self) -> libvirt.virConnect:
        """Get or create libvirt connection."""
        if self._conn is None or not self._is_connected():
            self._conn = libvirt.open(agent_settings.LIBVIRT_URI)
            if self._conn is None:
                raise RuntimeError(
                    f"Connection to libvirt failed: {agent_settings.LIBVIRT_URI}"
                )
        return self._conn

    def _is_connected(self) -> bool:
        """Check if the libvirt connection is still alive."""
        try:
            self._conn.getVersion()
            return True
        except Exception:
            return False

    def close(self):
        """Close the libvirt connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def define_vm(
        self,
        *,
        domain_name: str,
        vm_id: str | None = None,
        vcpus: int,
        ram_mb: int,
        disk_path: str,
        seed_path: str | None,
        mac_address: str,
        bridge: str = "br0",
        secondary_interfaces: list[dict] | None = None,
        iso_path: str | None = None,
        virtio_iso_path: str | None = None,
        boot_from_iso: bool = False,
        os_profile: str = "linux",
        vnc_port: int = -1,
        vnc_websocket_port: int = -1,
    ) -> str:
        """
        Define a new VM domain in libvirt.

        Automatically cleans up stale domains with the same name and
        detects available network configuration.

        Returns the domain UUID.
        """
        # Clean up stale domain with same name (from previous failed attempts)
        try:
            old_domain = self.conn.lookupByName(domain_name)
            if old_domain:
                if old_domain.isActive():
                    old_domain.destroy()
                self._undefine_domain(old_domain)
                print(f"[Libvirt] Stale domain '{domain_name}' cleaned up")
        except libvirt.libvirtError:
            pass  # Domain doesn't exist, that's fine

        # Detect network: check if bridge exists, fallback to libvirt NAT network
        resolved_bridge = self._resolve_network(bridge)

        # Build libvirt XML
        xml = self._build_domain_xml(
            domain_name=domain_name,
            vcpus=vcpus,
            ram_mb=ram_mb,
            disk_path=os.path.realpath(disk_path),
            seed_path=seed_path,
            mac_address=mac_address,
            bridge=resolved_bridge,
            secondary_interfaces=secondary_interfaces or [],
            iso_path=iso_path,
            virtio_iso_path=virtio_iso_path,
            boot_from_iso=boot_from_iso,
            os_profile=os_profile,
            vnc_port=vnc_port,
            vnc_websocket_port=vnc_websocket_port,
        )

        domain = self.conn.defineXML(xml)
        if domain is None:
            raise RuntimeError(f"Domain definition failed: {domain_name}")

        # Survive host reboots – without this VMs stay shut off after qwi restarts.
        try:
            domain.setAutostart(1)
        except libvirt.libvirtError as e:
            print(f"[Libvirt] Warning: autostart not set for {domain_name}: {e}")

        return domain.UUIDString()

    def _resolve_network(self, requested_bridge: str) -> str:
        """
        Check if the requested bridge exists on the host.
        Falls back to the default libvirt NAT network (virbr0) if not.
        Returns a special prefix 'network:' for libvirt virtual networks.
        """
        import os

        # Already a libvirt virtual network reference
        if requested_bridge.startswith("network:"):
            return requested_bridge

        # Check if the bridge device exists on the host
        if os.path.exists(f"/sys/class/net/{requested_bridge}"):
            return requested_bridge

        # Check common bridge names
        for fallback in ["virbr0", "br0", "bridge0"]:
            if os.path.exists(f"/sys/class/net/{fallback}"):
                print(f"[Libvirt] Bridge '{requested_bridge}' not found, using '{fallback}'")
                return fallback

        # No bridges found - try using libvirt's default virtual network
        try:
            net = self.conn.networkLookupByName("default")
            if net and net.isActive():
                print(f"[Libvirt] No bridge found, using libvirt 'default' network")
                return "network:default"
        except libvirt.libvirtError:
            pass

        # Last resort: return virbr0 and let libvirt handle the error
        print(f"[Libvirt] WARNING: No network bridge found!")
        return "virbr0"

    def get_available_networks(self) -> dict:
        """Get available network bridges and libvirt networks."""
        import os

        result = {"bridges": [], "libvirt_networks": []}

        # Scan host bridges
        net_path = "/sys/class/net"
        if os.path.exists(net_path):
            for iface in os.listdir(net_path):
                bridge_dir = os.path.join(net_path, iface, "bridge")
                brif_dir = os.path.join(net_path, iface, "brif")
                if os.path.exists(bridge_dir) or os.path.exists(brif_dir):
                    result["bridges"].append(iface)

        # Scan libvirt virtual networks
        try:
            for net in self.conn.listAllNetworks():
                result["libvirt_networks"].append({
                    "name": net.name(),
                    "active": net.isActive() == 1,
                    "autostart": net.autostart() == 1,
                })
        except Exception:
            pass

        return result

    def start_vm(self, domain_name: str, *, skip_iso_autoeject: bool = False) -> None:
        """Start a VM."""
        domain = self._get_domain(domain_name)
        if domain.isActive():
            return  # Already running
        if not skip_iso_autoeject:
            self._autoeject_install_iso_and_set_disk_boot(domain_name)
        result = domain.create()
        if result < 0:
            raise RuntimeError(f"VM start failed: {domain_name}")

    def stop_vm(self, domain_name: str) -> None:
        """Gracefully shutdown a VM (ACPI)."""
        domain = self._get_domain(domain_name)
        if not domain.isActive():
            return  # Already stopped
        domain.shutdown()

    def force_stop_vm(self, domain_name: str) -> None:
        """Force destroy a VM."""
        domain = self._get_domain(domain_name)
        if not domain.isActive():
            return
        domain.destroy()

    def reboot_vm(self, domain_name: str) -> None:
        """Reboot a VM."""
        domain = self._get_domain(domain_name)
        if not domain.isActive():
            raise RuntimeError(f"VM is not active: {domain_name}")
        domain.reboot(0)

    def delete_vm(self, domain_name: str) -> None:
        """Undefine (delete) a VM domain."""
        domain = self._get_domain(domain_name)
        if domain.isActive():
            domain.destroy()
        self._undefine_domain(domain)

    def _undefine_domain(self, domain) -> None:
        """Undefine a domain, including NVRAM for UEFI guests when present."""
        flags = 0
        for flag_name in ("VIR_DOMAIN_UNDEFINE_NVRAM", "VIR_DOMAIN_UNDEFINE_MANAGED_SAVE"):
            flags |= getattr(libvirt, flag_name, 0)
        try:
            if flags:
                domain.undefineFlags(flags)
            else:
                domain.undefine()
        except libvirt.libvirtError:
            domain.undefine()

    def get_vm_state(self, domain_name: str) -> dict:
        """Get VM state information."""
        domain = self._get_domain(domain_name)
        try:
            state, _ = domain.state()
        except libvirt.libvirtError:
            # Treat inaccessible runtime state as stopped for API stability.
            state = libvirt.VIR_DOMAIN_SHUTOFF

        state_map = {
            libvirt.VIR_DOMAIN_NOSTATE: "nostate",
            libvirt.VIR_DOMAIN_RUNNING: "running",
            libvirt.VIR_DOMAIN_BLOCKED: "blocked",
            libvirt.VIR_DOMAIN_PAUSED: "paused",
            libvirt.VIR_DOMAIN_SHUTDOWN: "shutting_down",
            libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
            libvirt.VIR_DOMAIN_CRASHED: "crashed",
            libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
        }

        try:
            is_active = domain.isActive() == 1
        except libvirt.libvirtError:
            is_active = False

        try:
            domain_uuid = domain.UUIDString()
        except libvirt.libvirtError:
            domain_uuid = ""

        try:
            vcpus = domain.maxVcpus()
        except libvirt.libvirtError:
            vcpus = 0

        try:
            ram_mb = domain.maxMemory() // 1024
        except libvirt.libvirtError:
            ram_mb = 0

        return {
            "name": domain_name,
            "state": state_map.get(state, "unknown"),
            "is_active": is_active,
            "uuid": domain_uuid,
            "vcpus": vcpus,
            "ram_mb": ram_mb,
        }

    def get_vm_vnc_port(self, domain_name: str) -> int | None:
        """Get the VNC port for a running VM."""
        domain = self._get_domain(domain_name)
        xml_str = domain.XMLDesc()
        root = ET.fromstring(xml_str)

        graphics = root.find(".//graphics[@type='vnc']")
        if graphics is not None:
            port = graphics.get("port")
            if port and port != "-1":
                return int(port)
        return None

    def eject_installer_media(self, domain_name: str) -> None:
        """Eject attached installer ISOs and set disk boot order."""
        domain = self._get_domain(domain_name)
        if domain.isActive():
            raise RuntimeError("VM must be stopped before ejecting installer media")
        self._autoeject_install_iso_and_set_disk_boot(domain_name)

    def attach_cdrom_iso(self, domain_name: str, iso_path: str) -> str:
        """Attach an ISO as additional CD-ROM to a stopped VM."""
        domain = self._get_domain(domain_name)
        if domain.isActive():
            raise RuntimeError("VM must be stopped before attaching CD-ROM media")

        xml_str = domain.XMLDesc()
        root = ET.fromstring(xml_str)
        devices = root.find("devices")
        if devices is None:
            raise RuntimeError("Domain XML missing devices section")

        # If already attached, return existing target.
        for disk in devices.findall("disk"):
            if disk.get("device") != "cdrom":
                continue
            source = disk.find("source")
            target = disk.find("target")
            src_file = source.get("file") if source is not None else None
            target_dev = target.get("dev") if target is not None else None
            if src_file == iso_path and target_dev:
                return target_dev

        used_targets: set[str] = set()
        for disk in devices.findall("disk"):
            if disk.get("device") != "cdrom":
                continue
            target = disk.find("target")
            if target is not None and target.get("dev"):
                used_targets.add(str(target.get("dev")))

        target_dev = None
        for ch in "abcdefghijklmnopqrstuvwxyz":
            candidate = f"sd{ch}"
            if candidate not in used_targets:
                target_dev = candidate
                break
        if target_dev is None:
            raise RuntimeError("No free CD-ROM target device available")

        new_disk = ET.Element("disk", {"type": "file", "device": "cdrom"})
        ET.SubElement(new_disk, "driver", {"name": "qemu", "type": "raw"})
        ET.SubElement(new_disk, "source", {"file": iso_path})
        ET.SubElement(new_disk, "target", {"dev": target_dev, "bus": "sata"})
        ET.SubElement(new_disk, "readonly")
        devices.append(new_disk)

        new_xml = ET.tostring(root, encoding="unicode")
        self.conn.defineXML(new_xml)
        return target_dev

    def list_domains(self) -> list[dict]:
        """List all defined domains."""
        domains = []
        for domain in self.conn.listAllDomains():
            state, _ = domain.state()
            domains.append({
                "name": domain.name(),
                "uuid": domain.UUIDString(),
                "is_active": domain.isActive() == 1,
                "state_code": state,
            })
        return domains

    def ensure_all_vm_autostart(self) -> int:
        """Enable libvirt autostart for all vm-* domains (survives host reboot)."""
        enabled = 0
        for domain in self.conn.listAllDomains():
            name = domain.name()
            if not name.startswith("vm-"):
                continue
            try:
                if domain.autostart() != 1:
                    domain.setAutostart(1)
                    enabled += 1
                    print(f"[Libvirt] autostart enabled for {name}")
            except libvirt.libvirtError as e:
                print(f"[Libvirt] Warning: autostart failed for {name}: {e}")
        return enabled

    def _get_domain(self, domain_name: str) -> libvirt.virDomain:
        """Get a domain by name."""
        try:
            return self.conn.lookupByName(domain_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"Domain not found: {domain_name}")

    def _build_domain_xml(
        self,
        *,
        domain_name: str,
        vcpus: int,
        ram_mb: int,
        disk_path: str,
        seed_path: str | None,
        mac_address: str,
        bridge: str,
        secondary_interfaces: list[dict],
        iso_path: str | None,
        virtio_iso_path: str | None,
        boot_from_iso: bool,
        os_profile: str = "linux",
        vnc_port: int,
        vnc_websocket_port: int,
    ) -> str:
        """Build the libvirt domain XML definition."""
        ram_kb = ram_mb * 1024
        is_windows = (os_profile or "").lower() == "windows"

        autoport_val = "yes" if vnc_port == -1 else "no"
        vnc_attrs = f'port="{vnc_port}" autoport="{autoport_val}"'
        if vnc_websocket_port > 0:
            vnc_attrs += f' websocket="{vnc_websocket_port}"'

        should_boot_from_iso = bool(iso_path) and boot_from_iso
        if is_windows:
            # Windows Server ISO: SeaBIOS + i440fx is the reliable installer path for
            # Microsoft evaluation ISOs on this stack. (OVMF often drops to the UEFI
            # shell and never starts setup.exe.) Use SATA system disk so install works
            # without loading viostor first; VirtIO ISO remains attached for NetKVM.
            os_xml = """  <os>
    <type arch='x86_64' machine='pc'>hvm</type>
    <boot dev='cdrom'/>
    <boot dev='hd'/>
    <bootmenu enable='yes'/>
  </os>"""
        elif should_boot_from_iso:
            os_xml = """  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='cdrom'/>
    <boot dev='hd'/>
    <bootmenu enable='yes'/>
  </os>"""
        else:
            # Legacy BIOS only (no UEFI). pc/i440fx + SeaBIOS for cloud images.
            os_xml = """  <os>
    <type arch='x86_64' machine='pc'>hvm</type>
    <boot dev='hd'/>
  </os>"""

        if is_windows:
            features_xml = """  <features>
    <acpi/>
    <apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
      <vpindex state='on'/>
      <runtime state='on'/>
      <synic state='on'/>
      <stimer state='on'/>
      <frequencies state='on'/>
    </hyperv>
  </features>"""
            clock_xml = """  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>"""
            tpm_xml = """
    <!-- TPM 2.0 for modern Windows Server -->
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>"""
        else:
            features_xml = """  <features>
    <acpi/>
    <apic/>
  </features>"""
            clock_xml = """  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>"""
            tpm_xml = ""

        # Windows uses SATA for the system disk (sda), so CD-ROMs start at sdb.
        cdrom_targets = ["sdb", "sdc", "sdd", "sde"] if is_windows else ["sda", "sdb", "sdc", "sdd"]
        cdrom_index = 0

        def _next_cdrom_target() -> str:
            nonlocal cdrom_index
            target = cdrom_targets[cdrom_index] if cdrom_index < len(cdrom_targets) else f"sd{chr(ord('a') + cdrom_index)}"
            cdrom_index += 1
            return target

        if is_windows:
            disk_xml = f"""
    <!-- Windows system disk on SATA (no VirtIO storage driver required during setup) -->
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='writeback'/>
      <source file='{disk_path}'/>
      <target dev='sda' bus='sata'/>
    </disk>"""
        else:
            # Boot order lives only in <os><boot .../></os>. Mixing that with
            # per-device <boot order='N'/> makes libvirt reject the domain XML.
            disk_xml = f"""
    <!-- Main disk (standalone cloned cloud image) -->
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='writeback'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='virtio'/>
    </disk>"""

        cdrom_disks_xml = []
        if iso_path:
            iso_target = _next_cdrom_target()
            cdrom_disks_xml.append(
                f"""
    <!-- Installation ISO (read-only) -->
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{iso_path}'/>
      <target dev='{iso_target}' bus='sata'/>
      <readonly/>
    </disk>"""
            )

        if virtio_iso_path:
            virtio_target = _next_cdrom_target()
            cdrom_disks_xml.append(
                f"""
    <!-- VirtIO Driver ISO (read-only) -->
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{virtio_iso_path}'/>
      <target dev='{virtio_target}' bus='sata'/>
      <readonly/>
    </disk>"""
            )

        # Cloud-init seed is Linux-only; never attach it to Windows ISO installs.
        if seed_path and not is_windows:
            seed_target = _next_cdrom_target()
            cdrom_disks_xml.append(
                f"""
    <!-- Cloud-Init seed (read-only) -->
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed_path}'/>
      <target dev='{seed_target}' bus='sata'/>
      <readonly/>
    </disk>"""
            )

        cdrom_xml = "".join(cdrom_disks_xml)

        # Windows gets e1000e (inbox driver). Linux keeps virtio.
        nic_model = "e1000e" if is_windows else "virtio"
        interfaces_xml = [self._build_network_xml(mac_address, bridge, model=nic_model)]
        for nic in secondary_interfaces:
            nic_mac = nic.get("mac_address")
            nic_network_name = nic.get("network_name")
            if not nic_mac or not nic_network_name:
                continue
            interfaces_xml.append(
                self._build_network_xml(nic_mac, f"network:{nic_network_name}", model=nic_model)
            )
        network_interfaces_xml = "\n    ".join(interfaces_xml)

        xml = f"""<domain type='kvm'>
  <name>{domain_name}</name>
  <memory unit='KiB'>{ram_kb}</memory>
  <currentMemory unit='KiB'>{ram_kb}</currentMemory>
  <vcpu placement='static'>{vcpus}</vcpu>
{os_xml}
{features_xml}
  <cpu mode='host-passthrough' check='none'/>
{clock_xml}
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
{disk_xml}
{cdrom_xml}
    <!-- Network interface(s) -->
    {network_interfaces_xml}
    <!-- VGA for cloud-image console output in VNC (in addition to serial) -->
    <video>
      <model type='vga' vram='16384' heads='1' primary='yes'/>
    </video>
    <!-- VNC Console -->
    <graphics type='vnc' {vnc_attrs} listen='{agent_settings.VNC_LISTEN_ADDRESS}'>
      <listen type='address' address='{agent_settings.VNC_LISTEN_ADDRESS}'/>
    </graphics>
    <!-- Serial console -->
    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <!-- Input devices -->
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <!-- Balloon for memory management -->
    <memballoon model='virtio'/>
    <!-- RNG for entropy -->
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
    </rng>
    <!-- Guest agent channel -->
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>{tpm_xml}
  </devices>
</domain>"""

        return xml

    def _autoeject_install_iso_and_set_disk_boot(self, domain_name: str) -> None:
        """
        If a powered-off domain still has an installation ISO attached,
        remove it and switch boot order to disk-only.

        This keeps the first ISO boot for installation, but avoids installer loops
        on subsequent starts after shutdown.
        """
        domain = self._get_domain(domain_name)
        if domain.isActive():
            return

        xml_str = domain.XMLDesc()
        root = ET.fromstring(xml_str)

        devices = root.find("devices")
        os_el = root.find("os")
        if devices is None or os_el is None:
            return

        removed_iso = False
        for disk in list(devices.findall("disk")):
            if disk.get("device") != "cdrom":
                continue
            source = disk.find("source")
            src_file = source.get("file") if source is not None else None
            if not src_file or not src_file.lower().endswith(".iso"):
                continue
            # Keep VirtIO driver media attached; only eject installer ISOs.
            base = os.path.basename(src_file).lower()
            if "virtio" in base:
                continue
            devices.remove(disk)
            removed_iso = True

        if not removed_iso:
            return

        # Replace any prior boot entries with disk boot only.
        for boot in list(os_el.findall("boot")):
            os_el.remove(boot)
        boot_el = ET.Element("boot")
        boot_el.set("dev", "hd")
        os_el.append(boot_el)

        bootmenu = os_el.find("bootmenu")
        if bootmenu is not None:
            os_el.remove(bootmenu)

        new_xml = ET.tostring(root, encoding="unicode")
        self.conn.defineXML(new_xml)

    def upsert_dhcp_host(
        self,
        *,
        network_name: str,
        mac_address: str,
        ip_address: str,
        hostname: str | None = None,
    ) -> None:
        """Add or replace a DHCP host reservation on a libvirt virtual network."""
        import subprocess

        net = self.conn.networkLookupByName(network_name)
        if net is None:
            raise RuntimeError(f"Network not found: {network_name}")

        # Ensure DHCP exists (older NAT networks were created without it).
        xml = net.XMLDesc(0)
        if "<dhcp>" not in xml:
            raise RuntimeError(
                f"Network '{network_name}' has no DHCP. Recreate/update the NAT network with DHCP enabled."
            )

        host_name = (hostname or "guest").replace("'", "")[:48]
        host_xml = f"<host mac='{mac_address}' name='{host_name}' ip='{ip_address}'/>"
        # Remove any previous reservation for this MAC, then add the new one.
        self.remove_dhcp_host(network_name=network_name, mac_address=mac_address)
        cmd = [
            "virsh",
            "net-update",
            network_name,
            "add",
            "ip-dhcp-host",
            host_xml,
            "--live",
            "--config",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to add DHCP host: {result.stderr.strip() or result.stdout.strip()}")

    def remove_dhcp_host(self, *, network_name: str, mac_address: str) -> None:
        """Remove a DHCP host reservation by MAC if present."""
        import subprocess
        import xml.etree.ElementTree as ET

        try:
            net = self.conn.networkLookupByName(network_name)
        except libvirt.libvirtError:
            return
        xml = net.XMLDesc(0)
        root = ET.fromstring(xml)
        for host in root.findall("./ip/dhcp/host"):
            if (host.get("mac") or "").lower() != mac_address.lower():
                continue
            attrs = " ".join(f"{k}='{v}'" for k, v in host.attrib.items())
            host_xml = f"<host {attrs}/>"
            subprocess.run(
                ["virsh", "net-update", network_name, "delete", "ip-dhcp-host", host_xml, "--live", "--config"],
                capture_output=True,
                text=True,
            )

    def _build_network_xml(self, mac_address: str, bridge: str, model: str = "virtio") -> str:
        """Build the network interface XML snippet.

        Supports both host bridges (type='bridge') and libvirt virtual
        networks (type='network') via the 'network:' prefix.
        """
        nic_model = model or "virtio"
        if bridge.startswith("network:"):
            # Libvirt virtual network (e.g. NAT via virbr0)
            net_name = bridge.split(":", 1)[1]
            return (
                f"<interface type='network'>\n"
                f"      <mac address='{mac_address}'/>\n"
                f"      <source network='{net_name}'/>\n"
                f"      <model type='{nic_model}'/>\n"
                f"    </interface>"
            )
        else:
            # Host bridge device
            return (
                f"<interface type='bridge'>\n"
                f"      <mac address='{mac_address}'/>\n"
                f"      <source bridge='{bridge}'/>\n"
                f"      <model type='{nic_model}'/>\n"
                f"    </interface>"
            )
