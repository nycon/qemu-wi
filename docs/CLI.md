# CLI (qemu-cli)

`qemu-cli` is maintained in a separate repository:

- **GitHub:** https://github.com/nycon/qemu-cli
- **Docs:** https://github.com/nycon/qemu-cli#readme
- **Releases:** https://github.com/nycon/qemu-cli/releases (use **≥ 1.1.0** for networks / public IPs)

## Install

Prebuilt binaries (recommended):

```bash
# Example: macOS Apple Silicon — see releases page for your platform
curl -fsSL -o qemu-cli.tar.gz \
  https://github.com/nycon/qemu-cli/releases/latest/download/qemu-cli_1.1.0_darwin_arm64.tar.gz
tar -xzf qemu-cli.tar.gz
chmod +x qemu-cli_1.1.0_darwin_arm64
sudo mv qemu-cli_1.1.0_darwin_arm64 /usr/local/bin/qemu-cli
qemu-cli version
```

From source:

```bash
git clone https://github.com/nycon/qemu-cli.git
cd qemu-cli
make install
```

Or:

```bash
go install github.com/nycon/qemu-cli@latest
```

## Quick Start

1. Create an API key in the panel: **User Profile → API Keys**
2. Create a context:

```bash
qemu-cli context create production \
  --url https://panel.example.com \
  --token qi_your_api_key
```

For self-signed TLS add `--insecure`.

3. Manage VMs, networks, and public IPs:

```bash
qemu-cli tenant use Default
qemu-cli vm list
qemu-cli vm start my-webserver

# NAT / Internal networks (no auto-NAT — create explicitly)
qemu-cli network create web-dmz --type nat
qemu-cli network list

# Public IPs assigned to the tenant
qemu-cli tenant public-ips
qemu-cli vm create web1 --image "Ubuntu 24.04" --project <uuid> --public-ip 159.69.172.28 --random-password

# Admin: assign free public IPs to a tenant
qemu-cli ip-pool available
qemu-cli tenant assign-public-ips Default --allocation-id <uuid>
qemu-cli tenant set-email ops@example.com
```

## Command map (1.1.0+)

| Area | Commands |
|------|----------|
| Context | `context create/list/use/delete/active` |
| VMs | `vm list/create/describe/start/stop/reboot/poweroff/delete/resize` |
| Snapshots / backups | `snapshot …`, `backup …` |
| Networks | `network list/create/update/delete` |
| Public IPs | `tenant public-ips`, `tenant assign-public-ips`, `ip-pool list/available` |
| Tenant | `tenant list/use/set-email` |
| Security | `firewall list/ban/unban/check/jails/set-jail/log` |
| Other | `storage-pool list`, `image list`, `user list`, `server-info`, `version` |

`vm create` networking: `--public-ip`, `--ip-allocation`, and/or `--ip-pool` (at least one network selection required).

Output: `-o table|json|yaml`. Shell completion: `qemu-cli completion zsh|bash|fish`.

Tenant users are isolated by panel membership: they only see their own tenants and can only act on VMs in those tenants.
