# CLI (qemu-cli)

`qemu-cli` is maintained in a separate repository:

- **GitHub:** https://github.com/nycon/qemu-cli
- **Docs:** https://github.com/nycon/qemu-cli#readme

## Install

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

Requires **qemu-cli ≥ 1.1.0** for `network`, `ip-pool`, `tenant public-ips` / `assign-public-ips` / `set-email`, and `vm create --public-ip` / `--ip-allocation`.

Tenant users are isolated by panel membership: they only see their own tenants and can only act on VMs in those tenants.
