# Hetzner Dedicated: `br0` Setup for Public VM IPs

This guide explains how to use **bridged networking (`br0`)** on **Hetzner Dedicated (Robot)** servers so VMs can get direct public IP addresses.

> This is for **Dedicated/Robot** setups with additional subnets.  
> Hetzner Cloud `/32` point-to-point setups are different and usually use NAT mode.

## Overview

Target topology:

```text
Internet <-> Host NIC/VLAN <-> br0 <-> VMs (public IPs from additional subnet)
```

## 1) Prepare network resources in Hetzner Robot

In Robot, set up:

1. A **vSwitch**
2. Attach your dedicated server to that vSwitch
3. Order/assign an additional **IPv4 subnet** (for example `/29`) to the vSwitch

Keep these values ready:

- VLAN ID
- Subnet/CIDR
- Gateway (as shown by Hetzner)

## 2) Install QEMU Interface normally

```bash
git clone https://github.com/nycon/qemu-wi.git /opt/qemu-interface
cd /opt/qemu-interface
sudo bash install.sh
```

If the installer detects a `/32` environment and suggests NAT only, finish the install first.  
You can configure `br0` manually afterward.

## 3) Configure VLAN + bridge on the host (Ubuntu/Netplan example)

Create a dedicated bridge for VM traffic.  
Do **not** replace the host's main management network unless you explicitly want that.

Example (`eno1` and VLAN ID must match your server):

```yaml
# /etc/netplan/60-vswitch-bridge.yaml
network:
  version: 2
  renderer: networkd

  vlans:
    eno1.4000:
      id: 4000
      link: eno1
      mtu: 1400

  bridges:
    br0:
      interfaces: [eno1.4000]
      mtu: 1400
      dhcp4: no
      dhcp6: no
```

Apply:

```bash
sudo netplan generate
sudo netplan apply
ip -br link show eno1.4000 br0
```

## 4) Configure IP Pool in QEMU Interface

In the panel:

1. Go to **Admin > Public IPs** (external pools)
2. Create an **External** pool with:
   - Subnet/CIDR = your additional Hetzner subnet
   - Gateway = exactly as provided by Hetzner
   - Bridge = `br0`
3. Assign this pool when creating VMs

## 5) Verify connectivity

Checks on host:

```bash
ip -br a
ip route
bridge link
```

Checks in VM:

- VM gets IP from your external pool
- Default route/gateway is correct
- VM can reach internet
- VM is reachable from outside (if firewall allows)

## 6) Common pitfalls

- Wrong VLAN ID
- Missing MTU adjustment (`1400` is common for Hetzner vSwitch)
- Wrong gateway in external pool
- Host firewall blocks traffic
- Cloud-init network config not matching pool settings

## 7) Troubleshooting quick commands

```bash
journalctl -u qemu-interface-agent -n 100 --no-pager
docker compose logs backend --tail 100
docker compose logs worker --tail 100
```

If VM has no network:

1. Re-check VLAN ID and netplan
2. Re-check pool gateway/CIDR in panel
3. Recreate VM network config (new test VM is often fastest)
