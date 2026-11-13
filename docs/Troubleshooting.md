# Troubleshooting

## Quick Diagnostics

```bash
# All services status
cd /opt/qemu-interface
docker compose ps
systemctl status qemu-interface-agent --no-pager

# Backend health
curl -s http://localhost:8000/health | python3 -m json.tool

# Host Agent health
curl -sk https://localhost:8443/health | python3 -m json.tool

# View logs
docker compose logs backend --tail 30
docker compose logs worker --tail 30
docker compose logs nginx --tail 30
docker compose logs db --tail 30
journalctl -u qemu-interface-agent -n 30 --no-pager
```

## Common Issues

### Backend won't start (SyntaxError / ImportError)

**Symptom**: `docker compose logs backend` shows Python errors

**Fix**: Rebuild the backend container:
```bash
cd /opt/qemu-interface
git pull origin main
docker compose up -d --build backend worker
```

### 502 Bad Gateway

**Symptom**: Browser shows 502 error

**Cause**: Backend is not running or Nginx can't reach it

**Fix**:
```bash
# Check if backend is running
docker compose ps

# If not running, restart
docker compose up -d

# Check backend logs
docker compose logs backend --tail 30
```

### Host Agent won't start (Address already in use)

**Symptom**: `journalctl -u qemu-interface-agent` shows `[Errno 98] address already in use`

**Cause**: An old process is still holding port 8443

**Fix**:
```bash
pkill -f "uvicorn agent.main"
sleep 2
systemctl restart qemu-interface-agent
```

### Host Agent won't start (Module not found)

**Symptom**: `ModuleNotFoundError` in agent logs

**Fix**:
```bash
cd /opt/qemu-interface/host-agent
source venv/bin/activate
pip install -r requirements.txt
deactivate
systemctl restart qemu-interface-agent
```

### VNC Console not working

**Symptom**: Console page shows error or blank screen

**Checks**:
```bash
# Is the VM running?
virsh list --all

# Does it have a VNC port?
virsh vncdisplay <domain-name>

# Is the host agent running?
systemctl status qemu-interface-agent

# Check VNC mapping
cat /var/lib/qemu-interface/vnc-mapping.json
```

**Fix**: Restart the host agent:
```bash
systemctl restart qemu-interface-agent
```

### Linux VM: VNC stuck at "Booting from Hard Disk…"

**Symptom**: Provision succeeds, but the console shows SeaBIOS and never a login. The VM may actually be booting on serial only (Ubuntu cloud default).

**Fix** (from QEMU Interface `main` after pull):

```bash
apt install -y guestfs-tools
cd /opt/qemu-interface && git pull
sudo systemctl restart qemu-interface-agent
docker compose restart worker backend
```

Then **delete the old VM** and create a **new** Linux VM (existing disks are not re-patched).

Recommended cloud images (amd64):

- Ubuntu 24.04: https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
- Ubuntu 22.04: https://cloud-images.ubuntu.com/releases/22.04/release/jammy-server-cloudimg-amd64.img
- Debian 12: https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2

Full list and panel steps: [Cloud Images](Cloud-Images.md).

Minimum: **2048 MB RAM**, **20 GB disk**.

### VM creation fails

**Symptom**: "VM provisioning failed" error

**Checks**:
```bash
# Check task status in the UI (Tasks page)

# Check worker logs
docker compose logs worker --tail 50

# Check host agent logs
journalctl -u qemu-interface-agent -n 50

# Check disk space
df -h /var/lib/qemu-interface/

# Check if base image exists
ls -la /var/lib/qemu-interface/storage/*.img
```

### Database connection error

**Symptom**: Backend logs show `connection refused` to PostgreSQL

**Fix**:
```bash
# Check if DB is running
docker compose ps db

# Restart DB
docker compose restart db

# Wait for health check, then restart backend
sleep 10
docker compose restart backend worker
```

### "Failed to load templates" / API errors

**Symptom**: Frontend shows loading errors

**Check**: Open browser dev tools (F12) → Network tab → look for red requests

**Common causes**:
- Backend not running → `docker compose up -d`
- Token expired → Log out and log back in
- Nginx misconfigured → `docker compose restart nginx`

### Alembic migration errors

**Symptom**: Backend logs show migration errors on startup

**Fix**:
```bash
docker compose build --no-cache backend
docker compose run --rm backend alembic upgrade head
```

If you get `DuplicateTableError` (table already exists but revision not stamped):
```bash
docker compose run --rm backend alembic stamp 20260320_project_users
docker compose run --rm backend alembic upgrade head
docker compose restart backend frontend
```

### Egress rules not working

**Symptom**: VMs can still access blocked ports

**Check**:
```bash
# Verify chain exists
iptables -L VM_EGRESS -n -v

# Verify FORWARD hooks
iptables -L FORWARD -n | grep VM_EGRESS

# Restart host agent to reapply
systemctl restart qemu-interface-agent
```

### SSL certificate issues

**Symptom**: HTTPS not working or certificate warnings

**Checks**:
```bash
# Check certificate
openssl s_client -connect your-domain.com:443 -servername your-domain.com < /dev/null 2>/dev/null | openssl x509 -noout -dates

# Check Nginx config
docker compose exec nginx nginx -t

# Re-setup SSL via UI or:
cd /opt/qemu-interface && sudo bash install.sh
```

### Port conflicts

**Symptom**: Container won't start, port already in use

**Check**:
```bash
# Find what's using the port
ss -tlnp | grep -E ":(80|443|3000|8000|8443) "
```

**Fix**: Stop the conflicting service or change port in `docker-compose.override.yml`.

## Log Locations

| Log | Location |
|---|---|
| Installer | `/var/log/qemu-interface-install.log` |
| Backend | `docker compose logs backend` |
| Worker | `docker compose logs worker` |
| Nginx | `docker compose logs nginx` |
| Database | `docker compose logs db` |
| Host Agent | `journalctl -u qemu-interface-agent` |
| Egress blocked | `journalctl -k \| grep VM_EGRESS_BLOCKED` |
| libvirt | `/var/log/libvirt/qemu/` |

## Reset Everything

**Warning**: This deletes all data!

```bash
cd /opt/qemu-interface
docker compose down -v
docker compose up -d --build
sleep 30
docker compose exec -T -e ADMIN_PASSWORD="Admin123!" backend python -m scripts.seed
```

### Networks page empty / cannot create a VM with a public IP

**Symptom**: Tenant **Networks** shows no NAT/Internal nets; create VM has few or no IPs.

**Cause** (by design):
- The panel does **not** auto-create a default NAT network for new tenants.
- Public addresses appear only after a superadmin assigns concrete reserved IPs (**Admin → Tenants** or `qemu-cli tenant assign-public-ips`).

**Fix**:
1. Superadmin: **Admin → Public IPs** (external pool) → assign IPs under **Admin → Tenants**.
2. Tenant admin: **Networks → Create** a NAT or Internal network if private networking is needed.
3. On VM create, pick one assigned public IP from the dropdown (or a NAT/Internal pool).

### Live metrics show N/A / guest agent missing

**Symptom**: CPU/RAM stats stay empty on a Linux cloud VM.

**Cause**: qemu-guest-agent not running inside the guest (older VMs created before auto-install).

**Fix** (VNC console):
```bash
sudo apt-get update && sudo apt-get install -y qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
```
New Linux cloud-image VMs install the agent automatically via cloud-init.

### SSH session drops after idle / reconnect hangs briefly

**Symptom**: After idle time SSH disconnects; VM still runs in the panel; reconnect is slow or fails.

**Cause**: Public IP + NAT idle timeout and/or SSH bot load — not the panel killing the VM.

**Product fix (guest, no client changes)**: New VMs get via cloud-init:
- `ClientAliveInterval 30` (sshd keeps the TCP session alive)
- `MaxStartups` / `UseDNS no` (faster, more reliable reconnect)
- `fail2ban` for sshd

Existing VMs – once via VNC console in the panel:

```bash
sudo tee /etc/ssh/sshd_config.d/99-qemu-interface.conf <<'EOF'
ClientAliveInterval 30
ClientAliveCountMax 10
TCPKeepAlive yes
UseDNS no
LoginGraceTime 60
MaxStartups 50:30:100
EOF
sudo apt-get update && sudo apt-get install -y fail2ban
sudo systemctl enable --now fail2ban
sudo systemctl restart ssh
```

**Emergency access**: Panel → VM → Console (VNC) when SSH is unreachable.

## Support

1. Check this troubleshooting guide
2. Check backend/worker/agent logs
3. Check the installer log: `cat /var/log/qemu-interface-install.log`
4. API docs (if enabled): set `EXPOSE_API_DOCS=true` then open `/api/docs`
