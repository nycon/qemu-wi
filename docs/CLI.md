# CLI (qcli)

`qcli` is maintained in a separate repository:

- **GitHub:** https://github.com/nycon/qcli
- **Docs:** https://github.com/nycon/qcli#readme
- **Releases:** https://github.com/nycon/qcli/releases (use **≥ 1.2.0** for networks / public IPs)

## Install

Prebuilt binaries (recommended):

```bash
# Example: macOS Apple Silicon — see releases page for your platform
curl -fsSL -o qcli.tar.gz \
  https://github.com/nycon/qcli/releases/latest/download/qcli_1.2.0_darwin_arm64.tar.gz
tar -xzf qcli.tar.gz
chmod +x qcli_1.2.0_darwin_arm64
sudo mv qcli_1.2.0_darwin_arm64 /usr/local/bin/qcli
qcli version
```

From source:

```bash
git clone https://github.com/nycon/qcli.git
cd qcli
make install
```

Or:

```bash
go install github.com/nycon/qcli@latest
```

## Quick Start

1. Create an API key in the panel: **User Profile → API Keys**
2. Create a context:

```bash
qcli context create production \
  --url https://panel.example.com \
  --token qi_your_api_key
```

For self-signed TLS add `--insecure`.

3. Manage VMs, networks, and public IPs:

```bash
qcli tenant use Default
qcli vm list
qcli vm start my-webserver

# NAT / Internal networks (no auto-NAT — create explicitly)
qcli network create web-dmz --type nat
qcli network list

# Public IPs assigned to the tenant
qcli tenant public-ips
qcli vm create web1 --image "Ubuntu 24.04" --project <uuid> --public-ip 159.69.172.28 --random-password

# Admin: assign free public IPs to a tenant
qcli ip-pool available
qcli tenant assign-public-ips Default --allocation-id <uuid>
qcli tenant set-email ops@example.com
```

## Command map (1.2.0+)

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

Output: `-o table|json|yaml`. Shell completion: `qcli completion zsh|bash|fish`.

Tenant users are isolated by panel membership: they only see their own tenants and can only act on VMs in those tenants.
