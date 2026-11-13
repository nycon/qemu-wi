# Installation Guide

## Requirements

### Hardware
- CPU with **KVM virtualization support** (Intel VT-x / AMD-V)
- Minimum 4 GB RAM (8+ GB recommended)
- 50+ GB disk space

### Software
- **OS**: Ubuntu 20.04+, Debian 11+, Rocky Linux 8+, AlmaLinux 8+
- **Root access** required

### Network
- **Public (firewall):** ports **80** and **443** only (web UI via Nginx)
- **Not public:** Host Agent **8443** (localhost + Docker bridge only – the installer does **not** open it on UFW/firewalld)
- **VNC console:** proxied through Nginx (`/websockify`) – do **not** open 6080–6180 to the internet

## Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/nycon/qemu-wi/main/install.sh | sudo bash
```

Or with a specific branch:

```bash
curl -fsSL https://raw.githubusercontent.com/nycon/qemu-wi/main/install.sh | sudo bash -s -- --branch develop
```

## Manual Install

```bash
git clone https://github.com/nycon/qemu-wi.git /opt/qemu-interface
cd /opt/qemu-interface
sudo bash install.sh
```

## Installer Phases

The installer runs through these phases:

| Phase | Name | What it does |
|---|---|---|
| 0 | Prerequisites | Checks root, OS detection, KVM support |
| 1 | System Packages | Installs Docker, git, rsync, qemu-utils, libvirt, etc. |
| 2 | Docker Setup | Starts Docker, enables on boot |
| 3 | KVM Check | Verifies `/dev/kvm` exists, loads kernel modules |
| 4 | Directories | Creates data dirs (`/var/lib/qemu-interface/`, `/etc/qemu-interface/`) |
| 5 | Repository | Clones or updates the git repository |
| 6 | Configuration | Generates secrets, `.env`, `docker-compose.override.yml` |
| 6b | Nginx Config | Generates Nginx config (HTTP or HTTPS based on certificates) |
| 7 | Docker Build | Builds and starts all containers |
| 8 | Host Agent | Installs Python venv, systemd service, TLS certificates |
| 9 | libvirt | Configures storage pools, bridges, default NAT network |
| 10 | Database | Waits for backend, runs migrations, seeds admin user |
| 11 | Verify | Health checks for all services |
| 12 | Bridge Check | Checks for `br0` bridge (optional, for public IPs) |
| 13 | Firewall | Opens **80/443** (and SSH if needed); keeps **8443** off the public internet; sets up VM egress filtering + Fail2Ban |
| 14 | Status | Final summary with login credentials |

## Idempotency

The installer is **fully idempotent** – you can run it multiple times safely:

- Existing secrets are **reused** (from `/etc/qemu-interface/secrets`)
- Database data is **preserved**
- Docker volumes are **not deleted**
- Configuration is regenerated but secrets stay the same

## Post-Install

After installation, the installer shows:

```
╔══════════════════════════════════════════╗
║   QEMU Interface – Installation Complete ║
╚══════════════════════════════════════════╝

  URL:       http://<server-ip>
  Username:  admin
  Password:  <generated-password>
```

## Updating

```bash
cd /opt/qemu-interface
git pull origin main
sudo bash install.sh
```

Alternative without re-running installer:

```bash
cd /opt/qemu-interface
git pull origin main
docker compose build --no-cache backend
docker compose run --rm backend alembic upgrade head
docker compose up -d --build backend frontend
docker compose restart backend frontend
sudo systemctl restart qemu-interface-agent
```

## SMTP Notes (Plesk and custom mail hosts)

- Configure SMTP in **Settings > SMTP Configuration** (superadmin).
- Prefer the mail host that matches the certificate hostname (CN/SAN), not just the main domain.
- Port `587` = STARTTLS, port `465` = implicit SSL.
- If your provider uses a mismatched cert hostname, enable **Allow invalid TLS certificate (insecure)** as a fallback.

## SSL Setup

### Automatic (Let's Encrypt)

```bash
# Point your domain's DNS A record to the server IP first
sudo certbot certonly --standalone -d your-domain.com --agree-tos -m your@email.com
# Re-run installer to detect certificates
cd /opt/qemu-interface && sudo bash install.sh
```

### Via Web Interface

Navigate to **Settings > SSL/Domain** in the admin panel to set up SSL directly from the UI.

### Custom Certificate

Place your files at:
```
/etc/letsencrypt/live/<domain>/fullchain.pem
/etc/letsencrypt/live/<domain>/privkey.pem
```

Then re-run `sudo bash install.sh`.

## Uninstall

```bash
cd /opt/qemu-interface
docker compose down -v          # Stop and remove containers + volumes
systemctl stop qemu-interface-agent
systemctl disable qemu-interface-agent
rm /etc/systemd/system/qemu-interface-agent.service
systemctl daemon-reload
rm -rf /opt/qemu-interface
rm -rf /var/lib/qemu-interface
rm -rf /etc/qemu-interface
```

**Warning**: This deletes ALL data including VM disks and database.
