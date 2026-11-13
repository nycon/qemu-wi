#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
#  QEMU Interface – Installation Script
#  Installs the complete virtualization panel on a bare-metal server
# ============================================================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="/opt/qemu-interface"
DATA_DIR="/var/lib/qemu-interface"
CONFIG_DIR="/etc/qemu-interface"
LOG_FILE="/var/log/qemu-interface-install.log"

# ============================================================================
#  Helper Functions
# ============================================================================

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo -e "${GREEN}[OK]${NC} $1"
    echo "$msg" >> "$LOG_FILE"
}

warn() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $1"
    echo -e "${YELLOW}[WARN]${NC} $1"
    echo "$msg" >> "$LOG_FILE"
}

error() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1"
    echo -e "${RED}[ERROR]${NC} $1"
    echo "$msg" >> "$LOG_FILE"
    exit 1
}

info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

header() {
    echo ""
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BOLD}${BLUE}  $1${NC}"
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

generate_secret() {
    openssl rand -hex 32
}

confirm() {
    local prompt="$1 [y/N]: "
    read -rp "$(echo -e "${YELLOW}${prompt}${NC}")" answer
    [[ "$answer" =~ ^[jJyY]$ ]]
}

# Step marker (no carriage-return – keeps live command output readable)
progress_bar() {
    local current=$1
    local total=$2
    local label=$3
    [[ "$total" -lt 1 ]] && total=1
    local pct=$(( current * 100 / total ))
    echo -e "${CYAN}── Step ${current}/${total} (${pct}%): ${label}${NC}"
}

# Run a command with live stdout/stderr on the terminal + install log
run_visible() {
    local msg="$1"
    shift
    info "$msg"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] \$ $*" >> "$LOG_FILE"
    "$@" 2>&1 | tee -a "$LOG_FILE"
    return "${PIPESTATUS[0]}"
}

# apt install batch with live package manager output
run_apt_install_batch() {
    local step=$1
    local total=$2
    local batch_label=$3
    shift 3
    progress_bar "$step" "$total" "$batch_label"
    info "Installing packages: ${batch_label}"
    echo "    packages: $*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] apt-get install batch: ${batch_label}" >> "$LOG_FILE"
    if apt-get install -y \
        -o Dpkg::Progress-Fancy=1 \
        -o APT::Color=1 \
        "$@" 2>&1 | tee -a "$LOG_FILE"; then
        log "Installed: ${batch_label}"
        return 0
    fi
    error "Package installation failed: ${batch_label}\n         See log: ${LOG_FILE}"
}

# dnf install batch with live package manager output
run_dnf_install_batch() {
    local step=$1
    local total=$2
    local batch_label=$3
    shift 3
    progress_bar "$step" "$total" "$batch_label"
    info "Installing packages: ${batch_label}"
    echo "    packages: $*"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] dnf install batch: ${batch_label}" >> "$LOG_FILE"
    if dnf install -y "$@" 2>&1 | tee -a "$LOG_FILE"; then
        log "Installed: ${batch_label}"
        return 0
    fi
    error "Package installation failed: ${batch_label}\n         See log: ${LOG_FILE}"
}

# ============================================================================
#  Update mode (after git pull on an existing install)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Lock CORS/ALLOWED_HOSTS to the panel SSL domain (install only — not --update).
# Call after ssl.json exists / SSL_DOMAIN is known. No-op if domain empty.
apply_panel_domain_cors() {
    local domain="${1:-}"
    domain=$(echo "${domain}" | tr '[:upper:]' '[:lower:]' | sed 's/\.$//' | tr -d '[:space:]')
    if [[ -z "$domain" || "$domain" == "localhost" ]]; then
        return 0
    fi
    # Skip wildcard / invalid
    if [[ "$domain" == "*" ]]; then
        return 0
    fi

    info "Locking CORS/ALLOWED_HOSTS to panel domain: ${domain}"
    local cors="[\"https://${domain}\"]"
    local hosts="[\"${domain}\",\"localhost\",\"127.0.0.1\"]"

    if [[ -f "${INSTALL_DIR}/backend/.env" ]]; then
        if grep -q '^CORS_ORIGINS=' "${INSTALL_DIR}/backend/.env"; then
            sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=${cors}|" "${INSTALL_DIR}/backend/.env"
        else
            echo "CORS_ORIGINS=${cors}" >> "${INSTALL_DIR}/backend/.env"
        fi
        if grep -q '^ALLOWED_HOSTS=' "${INSTALL_DIR}/backend/.env"; then
            sed -i "s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=${hosts}|" "${INSTALL_DIR}/backend/.env"
        else
            echo "ALLOWED_HOSTS=${hosts}" >> "${INSTALL_DIR}/backend/.env"
        fi
        if grep -q '^PANEL_DOMAIN=' "${INSTALL_DIR}/backend/.env"; then
            sed -i "s|^PANEL_DOMAIN=.*|PANEL_DOMAIN=${domain}|" "${INSTALL_DIR}/backend/.env"
        else
            echo "PANEL_DOMAIN=${domain}" >> "${INSTALL_DIR}/backend/.env"
        fi
    fi

    if [[ -f "${INSTALL_DIR}/docker-compose.override.yml" ]] && command -v python3 &>/dev/null; then
        PANEL_DOMAIN="$domain" INSTALL_DIR="${INSTALL_DIR}" python3 - <<'PY'
import os, re
path = os.path.join(os.environ.get("INSTALL_DIR", "/opt/qemu-interface"), "docker-compose.override.yml")
if not os.path.exists(path):
    raise SystemExit(0)
domain = os.environ["PANEL_DOMAIN"]
text = open(path).read()
cors = f"'[\"https://{domain}\"]'"
hosts = f"'[\"{domain}\",\"localhost\",\"127.0.0.1\"]'"
text = re.sub(r"CORS_ORIGINS:\s*'?\[[^\]]*\]'?", f"CORS_ORIGINS: {cors}", text)
text = re.sub(r"ALLOWED_HOSTS:\s*'?\[[^\]]*\]'?", f"ALLOWED_HOSTS: {hosts}", text)
if re.search(r"PANEL_DOMAIN:", text):
    text = re.sub(r'PANEL_DOMAIN:\s*"?[^"\n]*"?', f'PANEL_DOMAIN: "{domain}"', text)
else:
    text = text.replace(
        "ALLOW_PUBLIC_REGISTRATION:",
        f'PANEL_DOMAIN: "{domain}"\n      ALLOW_PUBLIC_REGISTRATION:',
        1,
    )
open(path, "w").write(text)
print("override CORS/hosts locked")
PY
    fi

    if [[ -f "${CONFIG_DIR}/secrets" ]]; then
        if grep -q '^PANEL_DOMAIN=' "${CONFIG_DIR}/secrets"; then
            sed -i "s|^PANEL_DOMAIN=.*|PANEL_DOMAIN=${domain}|" "${CONFIG_DIR}/secrets"
        else
            echo "PANEL_DOMAIN=${domain}" >> "${CONFIG_DIR}/secrets"
        fi
    fi

    log "CORS_ORIGINS=https://${domain} · ALLOWED_HOSTS=${domain},localhost,127.0.0.1"
}

run_update() {
    header "QEMU Interface – Update"
    cd "${INSTALL_DIR}"

    info "Syncing nginx platform config..."
    mkdir -p /etc/qemu-interface "${CONFIG_DIR}"
    if [[ ! -f /etc/qemu-interface/nginx-platform.conf ]]; then
        cp "${INSTALL_DIR}/deploy/nginx/platform-http.conf" /etc/qemu-interface/nginx-platform.conf
        log "Created /etc/qemu-interface/nginx-platform.conf"
    fi
    if command -v python3 &>/dev/null; then
        PYTHONPATH="${INSTALL_DIR}/host-agent" python3 "${INSTALL_DIR}/host-agent/scripts/render-nginx-platform.py" \
            >> "$LOG_FILE" 2>&1 || warn "nginx platform render failed – agent will retry on start"
    fi

    if [[ -f /etc/sysctl.d/99-qemu-interface.conf ]]; then
        sysctl -p /etc/sysctl.d/99-qemu-interface.conf >> "$LOG_FILE" 2>&1 || true
    fi

    info "Rebuilding and restarting containers..."
    docker compose build --progress=plain 2>&1 | tee -a "$LOG_FILE" || warn "docker compose build had warnings"
    docker compose up -d 2>&1 | tee -a "$LOG_FILE"

    info "Restarting host agent (VM autostart, firewall, port-forwards)..."
    systemctl daemon-reload 2>/dev/null || true
    systemctl restart qemu-interface-agent
    sleep 2
    if systemctl is-active --quiet qemu-interface-agent; then
        log "Host agent running"
    else
        warn "Host agent not active – check: journalctl -u qemu-interface-agent -n 30"
    fi

    echo ""
    log "Update complete"
    info "git pull && sudo bash install.sh --update"
    exit 0
}

if [[ "${1:-}" == "--update" ]]; then
    [[ $EUID -ne 0 ]] && error "Run: sudo bash install.sh --update"
    run_update
fi

# ============================================================================
#  Banner
# ============================================================================

clear
echo -e "${BOLD}${BLUE}"
cat << 'BANNER'

   ___  _____ __  __ _   _   ___       _             __
  / _ \| ____|  \/  | | | | |_ _|_ __ | |_ ___ _ __ / _| __ _  ___ ___
 | | | |  _| | |\/| | | | |  | || '_ \| __/ _ \ '__| |_ / _` |/ __/ _ \
 | |_| | |___| |  | | |_| |  | || | | | ||  __/ |  |  _| (_| | (_|  __/
  \__\_\_____|_|  |_|\___/  |___|_| |_|\__\___|_|  |_|  \__,_|\___\___|

  Virtualization Panel – Installation Script v1.0

BANNER
echo -e "${NC}"

# ============================================================================
#  Phase 0: Check Prerequisites
# ============================================================================

header "Phase 0: Check Prerequisites"

# Root check
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root.\n         Please run with 'sudo bash install.sh'."
fi

# Prepare log file
mkdir -p "$(dirname "$LOG_FILE")"
echo "=== QEMU Interface Installation $(date) ===" > "$LOG_FILE"
info "Live install output is shown here and also saved to: ${LOG_FILE}"

# Detect OS
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID}"
    OS_VERSION="${VERSION_ID}"
    OS_NAME="${PRETTY_NAME}"
else
    error "Could not detect operating system (/etc/os-release missing)."
fi

log "Operating system: ${OS_NAME}"

# Supported distributions
case "$OS_ID" in
    ubuntu|debian)
        PKG_MANAGER="apt"
        ;;
    rocky|alma|centos|rhel|fedora)
        PKG_MANAGER="dnf"
        ;;
    *)
        warn "Distribution '${OS_ID}' is not officially tested."
        if ! confirm "Continue anyway?"; then
            exit 1
        fi
        PKG_MANAGER="apt"  # Fallback
        ;;
esac

log "Package manager: ${PKG_MANAGER}"

# Architecture
ARCH=$(uname -m)
if [[ "$ARCH" != "x86_64" ]]; then
    error "Only x86_64 architecture is supported (detected: ${ARCH})."
fi

# KVM support
if [[ -e /dev/kvm ]]; then
    log "KVM support: available"
else
    warn "KVM support not detected (/dev/kvm missing)."
    warn "VMs will be very slow without hardware acceleration."
    if ! confirm "Continue anyway?"; then
        exit 1
    fi
fi

# Check RAM
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
if [[ $TOTAL_RAM_MB -lt 2048 ]]; then
    warn "Less than 2 GB RAM detected (${TOTAL_RAM_MB} MB). Recommended: at least 4 GB."
fi
log "RAM: ${TOTAL_RAM_MB} MB"

# Check disk
FREE_DISK_GB=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
if [[ $FREE_DISK_GB -lt 20 ]]; then
    warn "Less than 20 GB free disk space (${FREE_DISK_GB} GB)."
fi
log "Free disk space: ${FREE_DISK_GB} GB"

# ============================================================================
#  Phase 1: Install System Packages
# ============================================================================

header "Phase 1: Install System Packages"

APT_BATCH_TOTAL=5
DNF_BATCH_TOTAL=4

if [[ "$PKG_MANAGER" == "apt" ]]; then
    export DEBIAN_FRONTEND=noninteractive
    if ! run_visible "Updating package lists (apt-get update)..." apt-get update; then
        error "apt-get update failed. See log: ${LOG_FILE}"
    fi
    log "Package lists updated"
    echo ""
    info "Installing system packages in ${APT_BATCH_TOTAL} steps (live apt output below)..."
    echo ""

    run_apt_install_batch 1 "$APT_BATCH_TOTAL" "Base tools" \
        curl wget ripgrep git rsync ca-certificates gnupg lsb-release openssl certbot

    run_apt_install_batch 2 "$APT_BATCH_TOTAL" "Python" \
        python3 python3-pip python3-venv python3-dev

    run_apt_install_batch 3 "$APT_BATCH_TOTAL" "QEMU / KVM" \
        qemu-kvm qemu-utils ovmf

    run_apt_install_batch 4 "$APT_BATCH_TOTAL" "libvirt & networking" \
        libvirt-daemon-system libvirt-clients libvirt-dev virtinst bridge-utils

    run_apt_install_batch 5 "$APT_BATCH_TOTAL" "Cloud-Init, build & security" \
        cloud-image-utils genisoimage guestfs-tools iptables-persistent fail2ban pkg-config gcc libpq-dev

    echo ""
    log "All system packages installed"

elif [[ "$PKG_MANAGER" == "dnf" ]]; then
    if ! run_visible "Updating package lists (dnf update)..." dnf update -y; then
        error "dnf update failed. See log: ${LOG_FILE}"
    fi
    log "Package lists updated"
    echo ""
    info "Installing system packages in ${DNF_BATCH_TOTAL} steps (live dnf output below)..."
    echo ""

    run_dnf_install_batch 1 "$DNF_BATCH_TOTAL" "Base tools" \
        curl wget ripgrep git rsync ca-certificates openssl

    run_dnf_install_batch 2 "$DNF_BATCH_TOTAL" "Python" \
        python3 python3-pip python3-devel

    run_dnf_install_batch 3 "$DNF_BATCH_TOTAL" "QEMU / KVM & libvirt" \
        qemu-kvm qemu-img libvirt libvirt-client libvirt-devel virt-install bridge-utils cloud-utils genisoimage libguestfs-tools edk2-ovmf

    run_dnf_install_batch 4 "$DNF_BATCH_TOTAL" "Build & security" \
        fail2ban gcc libpq-devel

    echo ""
    log "All system packages installed"
fi

# ============================================================================
#  Phase 2: Install Docker
# ============================================================================

header "Phase 2: Install Docker"

if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
    log "Docker already installed: v${DOCKER_VERSION}"
else
    info "Installing Docker (may take a few minutes)..."

    if [[ "$PKG_MANAGER" == "apt" ]]; then
        progress_bar 1 3 "Docker repository setup"
        install -m 0755 -d /etc/apt/keyrings
        info "Downloading Docker GPG key..."
        curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" \
            -o /etc/apt/keyrings/docker.asc 2>&1 | tee -a "$LOG_FILE"
        chmod a+r /etc/apt/keyrings/docker.asc

        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
            https://download.docker.com/linux/${OS_ID} \
            $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
            > /etc/apt/sources.list.d/docker.list
        log "Docker apt repository configured"

        if ! run_visible "Refreshing package lists for Docker..." apt-get update; then
            error "apt-get update (Docker repo) failed. See log: ${LOG_FILE}"
        fi

        progress_bar 2 3 "Docker packages"
        info "Installing Docker Engine and Compose plugin..."
        if ! apt-get install -y \
            -o Dpkg::Progress-Fancy=1 \
            -o APT::Color=1 \
            docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
            2>&1 | tee -a "$LOG_FILE"; then
            error "Docker installation failed. See log: ${LOG_FILE}"
        fi
        progress_bar 3 3 "Docker packages done"

    elif [[ "$PKG_MANAGER" == "dnf" ]]; then
        progress_bar 1 2 "Docker repository"
        run_visible "Adding Docker CE repository..." \
            dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo \
            || error "Docker repo setup failed. See log: ${LOG_FILE}"
        progress_bar 2 2 "Docker packages"
        info "Installing Docker Engine and Compose plugin..."
        if ! dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
            2>&1 | tee -a "$LOG_FILE"; then
            error "Docker installation failed. See log: ${LOG_FILE}"
        fi
    fi

    info "Starting Docker service..."
    run_visible "systemctl enable --now docker" systemctl enable --now docker \
        || error "Failed to start Docker. See log: ${LOG_FILE}"
    log "Docker installed and started"
fi

# Check Docker Compose
if docker compose version &> /dev/null; then
    COMPOSE_VERSION=$(docker compose version --short 2>/dev/null)
    log "Docker Compose: v${COMPOSE_VERSION}"
else
    error "Docker Compose plugin not found. Please install manually."
fi

# ============================================================================
#  Phase 3: Configure libvirt
# ============================================================================

header "Phase 3: Configure libvirt"

run_visible "Enabling and starting libvirtd..." systemctl enable --now libvirtd \
    || error "Failed to start libvirtd. See log: ${LOG_FILE}"
log "libvirtd enabled and started"

# Check if default network exists
if virsh net-info default &> /dev/null 2>&1; then
    log "libvirt default network available"
else
    warn "libvirt default network not found. Creating..."
    virsh net-define /dev/stdin <<NETXML >> "$LOG_FILE" 2>&1
<network>
  <name>default</name>
  <forward mode='nat'/>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.122.2' end='192.168.122.254'/>
    </dhcp>
  </ip>
</network>
NETXML
    virsh net-start default >> "$LOG_FILE" 2>&1
    virsh net-autostart default >> "$LOG_FILE" 2>&1
    log "libvirt default network created"
fi

# ============================================================================
#  Phase 4: Create Directories
# ============================================================================

header "Phase 4: Create Directories"

mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/config"
mkdir -p "$INSTALL_DIR/deploy/nginx/certs"
mkdir -p "$DATA_DIR"/{storage,images,cloud-init,backups,exports}
mkdir -p "$CONFIG_DIR"
mkdir -p /var/log/qemu-interface
mkdir -p /var/lib/letsencrypt
mkdir -p /var/lib/qemu-interface/proxy-configs
mkdir -p /var/lib/qemu-interface/lb-configs
# Keep include globs valid even with zero configs
touch /var/lib/qemu-interface/proxy-configs/.keep
touch /var/lib/qemu-interface/lb-configs/.keep
# Placeholder so nginx include stream_*.conf / http_*.conf never fails empty on some builds
: > /var/lib/qemu-interface/lb-configs/http_.conf
: > /var/lib/qemu-interface/lb-configs/stream_.conf

log "Install directory: ${INSTALL_DIR}"
log "Data directory: ${DATA_DIR}"
log "Config directory: ${CONFIG_DIR}"

# Libvirt QEMU must access cloud images (backing files) under DATA_DIR
if id libvirt-qemu &>/dev/null; then
    chown -R libvirt-qemu:kvm "$DATA_DIR" 2>/dev/null || true
    chmod -R g+rwX "$DATA_DIR" 2>/dev/null || true
    log "Data directory ownership set for libvirt-qemu"
fi

if [[ -d /etc/apparmor.d ]]; then
    # Prefer libvirt-qemu.d drop-in (supported path). Avoid a broken
    # #include to a non-existent abstractions/ file – that makes
    # virt-aa-helper / apparmor_parser fail and blocks VM start.
    AA_DROPIN_DIR="/etc/apparmor.d/abstractions/libvirt-qemu.d"
    AA_DROPIN="${AA_DROPIN_DIR}/qemu-interface"
    mkdir -p "$AA_DROPIN_DIR" /etc/apparmor.d/local/abstractions
    cat > "$AA_DROPIN" <<APPARMOR
# qemu-interface: allow QEMU to read cloud images and VM overlays
${DATA_DIR}/ r,
${DATA_DIR}/** rwk,
APPARMOR
    cat > /etc/apparmor.d/local/abstractions/libvirt-qemu <<APPARMOR
# qemu-interface (local override, included by abstractions/libvirt-qemu)
${DATA_DIR}/ r,
${DATA_DIR}/** rwk,
APPARMOR
    # Remove legacy broken include if present from older installs
    if [[ -f /etc/apparmor.d/abstractions/libvirt-qemu ]] \
        && grep -q 'libvirt-qemu-qemu-interface' /etc/apparmor.d/abstractions/libvirt-qemu 2>/dev/null; then
        sed -i '/libvirt-qemu-qemu-interface/d' /etc/apparmor.d/abstractions/libvirt-qemu
    fi
    systemctl reload apparmor >> "$LOG_FILE" 2>&1 || true
    log "AppArmor rules for ${DATA_DIR} installed"
fi

# ============================================================================
#  Phase 5: Deploy Source Code
# ============================================================================

header "Phase 5: Deploy Source Code"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "${SCRIPT_DIR}/docker-compose.yml" || ! -d "${SCRIPT_DIR}/backend" || ! -d "${SCRIPT_DIR}/frontend" ]]; then
    error "Repository not found. Please clone the repo first:\n  git clone https://github.com/nycon/qemu-wi.git ${INSTALL_DIR}\n  cd ${INSTALL_DIR} && sudo bash install.sh"
fi

if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
    info "Copying source to ${INSTALL_DIR}..."
    rsync -a --info=progress2 --exclude='*.env' --exclude='docker-compose.override.yml' \
        "${SCRIPT_DIR}/" "${INSTALL_DIR}/" 2>&1 | tee -a "$LOG_FILE"
    log "Source copied to ${INSTALL_DIR}"
else
    log "Running from ${INSTALL_DIR}"
fi

cd "$INSTALL_DIR"

# ============================================================================
#  Phase 6: Generate Configuration
# ============================================================================

header "Phase 6: Generate Configuration"

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

# --- Secrets: reuse existing OR generate new ones ---
if [[ -f "${CONFIG_DIR}/secrets" ]]; then
    info "Existing secrets found – reusing them"
    # shellcheck disable=SC1090
    source "${CONFIG_DIR}/secrets"
    # Generate only missing secrets (e.g. after update)
    SECRET_KEY="${SECRET_KEY:-$(generate_secret)}"
    JWT_SECRET="${JWT_SECRET:-$(generate_secret)}"
    AGENT_TOKEN="${AGENT_TOKEN:-$(generate_secret)}"
    DB_PASSWORD="${DB_PASSWORD:-$(generate_secret | head -c 24)}"
    ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)}"
    log "Existing secrets loaded"
else
    info "First install – generating new secrets"
    SECRET_KEY=$(generate_secret)
    JWT_SECRET=$(generate_secret)
    AGENT_TOKEN=$(generate_secret)
    DB_PASSWORD=$(generate_secret | head -c 24)
    ADMIN_PASSWORD=$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)
    log "All secrets generated"
fi

log "Server IP detected: ${SERVER_IP}"

# --- Backend .env ---
cat > "${INSTALL_DIR}/backend/.env" <<ENVFILE
# ===== Auto-generated by install.sh ($(date)) =====
# WARNING: Do not edit manually! Secrets are stored in ${CONFIG_DIR}/secrets

# Application
APP_NAME=QEMU Interface
APP_VERSION=0.1.0
DEBUG=false
SECRET_KEY=${SECRET_KEY}

# Database
DATABASE_URL=postgresql+asyncpg://qemu:${DB_PASSWORD}@postgres:5432/qemu_interface

# Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# JWT
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# Host Agent (inside Docker → host.docker.internal)
HOST_AGENT_URL=https://host.docker.internal:8443
HOST_AGENT_TOKEN=${AGENT_TOKEN}

# Storage
DEFAULT_STORAGE_POOL_PATH=/var/lib/qemu-interface/storage
CLOUD_IMAGES_PATH=/var/lib/qemu-interface/images
CLOUD_INIT_PATH=/var/lib/qemu-interface/cloud-init

# Network
DEFAULT_BRIDGE=br0

# CORS & Hosts – start permissive (*). Locked to https://<domain> when:
#   (1) install finds an existing LE cert (Phase 6b → apply_panel_domain_cors), or
#   (2) admin defines the domain in Settings → SSL (host-agent lock_cors_to_panel_domain).
# Not applied on install.sh --update.
CORS_ORIGINS=["*"]
ALLOWED_HOSTS=["*"]

# Public hardening defaults (production-safe)
ALLOW_PUBLIC_REGISTRATION=false
EXPOSE_API_DOCS=false
ENVFILE

log "Backend .env created"

# --- Host Agent .env ---
cat > "${INSTALL_DIR}/host-agent/.env" <<ENVFILE
# ===== Auto-generated by install.sh ($(date)) =====
# 0.0.0.0 so Docker (host.docker.internal → host-gateway) can reach the agent.
# Public ingress is blocked by scripts/lock-agent-port.sh (always applied below).
AGENT_HOST=0.0.0.0
AGENT_PORT=8443
AGENT_TOKEN=${AGENT_TOKEN}
TLS_CERT_FILE=${CONFIG_DIR}/agent-cert.pem
TLS_KEY_FILE=${CONFIG_DIR}/agent-key.pem
LIBVIRT_URI=qemu:///system
STORAGE_BASE_PATH=${DATA_DIR}/storage
CLOUD_IMAGES_PATH=${DATA_DIR}/images
CLOUD_INIT_PATH=${DATA_DIR}/cloud-init
DEFAULT_BRIDGE=br0
VNC_LISTEN_ADDRESS=127.0.0.1
CONTROL_PLANE_URL=http://localhost:8000
JWT_SECRET_KEY=${JWT_SECRET}
ENVFILE

log "Host Agent .env created"

# --- docker-compose.override.yml (all secrets + configuration) ---
cat > "${INSTALL_DIR}/docker-compose.override.yml" <<DCFILE
services:
  postgres:
    environment:
      POSTGRES_PASSWORD: "${DB_PASSWORD}"
  backend:
    environment:
      DATABASE_URL: "postgresql+asyncpg://qemu:${DB_PASSWORD}@postgres:5432/qemu_interface"
      SECRET_KEY: "${SECRET_KEY}"
      JWT_SECRET_KEY: "${JWT_SECRET}"
      HOST_AGENT_URL: "https://host.docker.internal:8443"
      HOST_AGENT_TOKEN: "${AGENT_TOKEN}"
      CORS_ORIGINS: '["*"]'
      ALLOWED_HOSTS: '["*"]'
      JWT_ACCESS_TOKEN_EXPIRE_MINUTES: "60"
      JWT_REFRESH_TOKEN_EXPIRE_DAYS: "30"
      ALLOW_PUBLIC_REGISTRATION: "false"
      EXPOSE_API_DOCS: "false"
    volumes:
      - ${DATA_DIR}/images:/var/lib/qemu-interface/images
      - ${DATA_DIR}/cloud-init:/var/lib/qemu-interface/cloud-init
      - ${DATA_DIR}/storage:/var/lib/qemu-interface/storage
      - ${DATA_DIR}/backups:/var/lib/qemu-interface/backups
  worker:
    environment:
      DATABASE_URL: "postgresql+asyncpg://qemu:${DB_PASSWORD}@postgres:5432/qemu_interface"
      SECRET_KEY: "${SECRET_KEY}"
      JWT_SECRET_KEY: "${JWT_SECRET}"
      HOST_AGENT_URL: "https://host.docker.internal:8443"
      HOST_AGENT_TOKEN: "${AGENT_TOKEN}"
    volumes:
      - ${DATA_DIR}/images:/var/lib/qemu-interface/images
      - ${DATA_DIR}/cloud-init:/var/lib/qemu-interface/cloud-init
      - ${DATA_DIR}/storage:/var/lib/qemu-interface/storage
      - ${DATA_DIR}/backups:/var/lib/qemu-interface/backups
DCFILE

log "Docker Compose override created (all secrets injected)"

# Save secrets to protected file
cat > "${CONFIG_DIR}/secrets" <<SECRETS
# QEMU Interface Secrets – generated $(date)
# WARNING: This file contains sensitive data!
DB_PASSWORD=${DB_PASSWORD}
SECRET_KEY=${SECRET_KEY}
JWT_SECRET=${JWT_SECRET}
AGENT_TOKEN=${AGENT_TOKEN}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
SERVER_IP=${SERVER_IP}
SECRETS
chmod 600 "${CONFIG_DIR}/secrets"
log "Secrets saved to ${CONFIG_DIR}/secrets (chmod 600)"

# ============================================================================
#  Phase 6b: Generate Nginx Configuration
# ============================================================================

header "Phase 6b: Nginx Configuration"

# Detect existing Let's Encrypt or custom certificate
SSL_FOUND=false
SSL_CERT=""
SSL_KEY=""

# Check Let's Encrypt certificates for all domains
if [[ -d /etc/letsencrypt/live ]]; then
    for CERT_DIR in /etc/letsencrypt/live/*/; do
        if [[ -f "${CERT_DIR}fullchain.pem" && -f "${CERT_DIR}privkey.pem" ]]; then
            SSL_CERT="${CERT_DIR}fullchain.pem"
            SSL_KEY="${CERT_DIR}privkey.pem"
            SSL_FOUND=true
            SSL_DOMAIN="$(basename "$CERT_DIR")"
            log "Let's Encrypt certificate found: ${SSL_DOMAIN}"
            break
        fi
    done
fi

# Check custom certs in deploy/nginx/certs
if [[ "$SSL_FOUND" != "true" ]]; then
    for ext in pem crt; do
        if ls "${INSTALL_DIR}/deploy/nginx/certs/"*.${ext} &>/dev/null 2>&1; then
            SSL_CERT=$(ls "${INSTALL_DIR}/deploy/nginx/certs/"*.${ext} 2>/dev/null | head -1)
            SSL_KEY=$(ls "${INSTALL_DIR}/deploy/nginx/certs/"*.key 2>/dev/null | head -1)
            if [[ -n "$SSL_CERT" && -n "$SSL_KEY" ]]; then
                SSL_FOUND=true
                log "Custom SSL certificate found: ${SSL_CERT}"
            fi
            break
        fi
    done
fi

mkdir -p /etc/qemu-interface "${INSTALL_DIR}/config"

if [[ "$SSL_FOUND" == "true" ]]; then
    log "SSL enabled – writing platform HTTPS config"
    cat > "${INSTALL_DIR}/config/ssl.json" <<SSLJSON
{
  "domain": "${SSL_DOMAIN:-localhost}",
  "ssl_enabled": true,
  "cert_path": "${SSL_CERT}",
  "key_path": "${SSL_KEY}"
}
SSLJSON
    if command -v python3 &>/dev/null; then
        PYTHONPATH="${INSTALL_DIR}/host-agent" python3 "${INSTALL_DIR}/host-agent/scripts/render-nginx-platform.py" >> "$LOG_FILE" 2>&1 \
            || warn "Could not render HTTPS platform config – will retry on agent start"
    fi
    # Same lock as live public go-live: CORS/Hosts → SSL domain (not on --update)
    apply_panel_domain_cors "${SSL_DOMAIN:-}"
else
    log "No SSL certificate found – HTTP-only platform config"
    info "SSL can be configured later in Settings."
    # Keep existing platform conf if present (e.g. re-run of install.sh).
    # Only seed HTTP template when missing.
    if [[ ! -f /etc/qemu-interface/nginx-platform.conf ]]; then
        if [[ -f "${INSTALL_DIR}/deploy/nginx/platform-http.conf" ]]; then
            cp "${INSTALL_DIR}/deploy/nginx/platform-http.conf" /etc/qemu-interface/nginx-platform.conf
            log "Created /etc/qemu-interface/nginx-platform.conf (HTTP)"
        else
            warn "platform-http.conf missing – nginx may fail until agent starts"
        fi
    else
        log "Keeping existing /etc/qemu-interface/nginx-platform.conf"
    fi
    # Clear ssl.json ssl_enabled if no cert (optional re-render to HTTP)
    if [[ -f "${INSTALL_DIR}/config/ssl.json" ]] && command -v python3 &>/dev/null; then
        PYTHONPATH="${INSTALL_DIR}/host-agent" python3 - <<'PY' >> "$LOG_FILE" 2>&1 || true
import json, os
p = "/opt/qemu-interface/config/ssl.json"
if os.path.exists(p):
    with open(p) as f:
        cfg = json.load(f)
    if cfg.get("ssl_enabled") and not (
        cfg.get("cert_path") and os.path.exists(cfg.get("cert_path", ""))
    ):
        cfg["ssl_enabled"] = False
        with open(p, "w") as f:
            json.dump(cfg, f, indent=2)
PY
    fi
fi

# Ensure platform file always exists before docker compose up
if [[ ! -f /etc/qemu-interface/nginx-platform.conf ]]; then
    if [[ -f "${INSTALL_DIR}/deploy/nginx/platform-http.conf" ]]; then
        cp "${INSTALL_DIR}/deploy/nginx/platform-http.conf" /etc/qemu-interface/nginx-platform.conf
    else
        error "Missing nginx platform config. Expected deploy/nginx/platform-http.conf"
    fi
fi

log "Nginx: deploy/nginx/nginx.conf (git) + /etc/qemu-interface/nginx-platform.conf (host)"

# ============================================================================
#  Phase 7: TLS Certificates for Host Agent
# ============================================================================

header "Phase 7: Create TLS Certificates"

HOSTNAME_FQDN=$(hostname -f 2>/dev/null || hostname)
REGEN_CERT=false

if [[ -f "${CONFIG_DIR}/agent-cert.pem" ]]; then
    # Check if existing cert includes host.docker.internal SAN
    if openssl x509 -in "${CONFIG_DIR}/agent-cert.pem" -noout -text 2>/dev/null | grep -q "host.docker.internal"; then
        log "TLS certificates already exist (SANs valid)"
    else
        warn "TLS certificate missing host.docker.internal SAN — regenerating"
        REGEN_CERT=true
    fi
else
    REGEN_CERT=true
fi

if [[ "$REGEN_CERT" == "true" ]]; then
    info "Creating self-signed TLS certificates for Host Agent..."
    openssl req -x509 \
        -newkey rsa:4096 \
        -keyout "${CONFIG_DIR}/agent-key.pem" \
        -out "${CONFIG_DIR}/agent-cert.pem" \
        -days 3650 \
        -nodes \
        -subj "/C=DE/ST=Berlin/L=Berlin/O=QEMU Interface/CN=${HOSTNAME_FQDN}" \
        -addext "subjectAltName=DNS:localhost,DNS:host.docker.internal,DNS:${HOSTNAME_FQDN},IP:127.0.0.1" \
        2>&1 | tee -a "$LOG_FILE"

    chmod 600 "${CONFIG_DIR}/agent-key.pem"
    chmod 644 "${CONFIG_DIR}/agent-cert.pem"
    log "TLS certificates created with SANs: localhost, host.docker.internal, ${HOSTNAME_FQDN}, 127.0.0.1"
fi

# ============================================================================
#  Phase 8: Install Host Agent (Python venv + systemd)
# ============================================================================

header "Phase 8: Install Host Agent"

VENV_DIR="${INSTALL_DIR}/host-agent/venv"

info "Creating Python virtual environment..."
progress_bar 1 3 "Python venv"
run_visible "python3 -m venv ${VENV_DIR}" python3 -m venv "$VENV_DIR" \
    || error "Failed to create venv. See log: ${LOG_FILE}"
log "venv created: ${VENV_DIR}"

progress_bar 2 3 "pip upgrade"
run_visible "Upgrading pip..." "${VENV_DIR}/bin/pip" install --upgrade pip \
    || warn "pip upgrade reported errors (continuing)"

progress_bar 3 3 "Host Agent dependencies"
info "Installing Host Agent Python packages (live output below)..."
if "${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/host-agent/requirements.txt" 2>&1 | tee -a "$LOG_FILE"; then
    log "Host Agent dependencies installed"
else
    warn "Some Host Agent dependencies could not be installed."
    warn "libvirt-python requires: apt install libvirt-dev pkg-config"
    warn "Check log: ${LOG_FILE}"
fi

# systemd service
cat > /etc/systemd/system/qemu-interface-agent.service <<SERVICE
[Unit]
Description=QEMU Interface Host Agent
After=network-online.target libvirtd.service
Wants=network-online.target
Requires=libvirtd.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=${INSTALL_DIR}/host-agent
EnvironmentFile=${INSTALL_DIR}/host-agent/.env
# Docker host-gateway needs a non-loopback listen address; firewall locks public access.
ExecStart=${VENV_DIR}/bin/uvicorn agent.main:app \\
    --host 0.0.0.0 \\
    --port 8443 \\
    --ssl-keyfile ${CONFIG_DIR}/agent-key.pem \\
    --ssl-certfile ${CONFIG_DIR}/agent-cert.pem \\
    --log-level info
Restart=always
RestartSec=5
LimitNOFILE=65536
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

# Always-on iptables lockdown for 8443 (works even when UFW is inactive)
install -d -m 0755 "${INSTALL_DIR}/scripts"
chmod 0755 "${INSTALL_DIR}/scripts/lock-agent-port.sh"
cat > /etc/systemd/system/qemu-interface-agent-firewall.service <<'FWService'
[Unit]
Description=QEMU Interface Host Agent port lockdown (8443)
After=network-online.target
Wants=network-online.target
Before=qemu-interface-agent.service

[Service]
Type=oneshot
ExecStart=/opt/qemu-interface/scripts/lock-agent-port.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
FWService

run_visible "systemctl daemon-reload" systemctl daemon-reload || true
run_visible "Enabling agent firewall lockdown..." systemctl enable --now qemu-interface-agent-firewall \
    || warn "Could not enable agent firewall unit (8443 lockdown)"
run_visible "Enabling qemu-interface-agent..." systemctl enable qemu-interface-agent \
    || error "Failed to enable host agent service. See log: ${LOG_FILE}"
log "Host Agent systemd service installed and enabled"

# ============================================================================
#  Phase 9: Build and Start Docker Containers
# ============================================================================

header "Phase 9: Start Docker Containers"

cd "$INSTALL_DIR"

info "Building Docker images (often 10–20 minutes on first install)..."
progress_bar 1 2 "Docker build (see output below)"
echo ""
info "Build progress (live output):"
echo ""
if docker compose build --progress=plain 2>&1 | tee -a "$LOG_FILE"; then
    progress_bar 2 2 "Docker images built"
    log "Docker images built"
else
    echo ""
    error "Docker build failed! See output above for details.\n         Log: ${LOG_FILE}"
fi

echo ""
info "Starting services..."
if docker compose up -d 2>&1 | tee -a "$LOG_FILE"; then
    log "Docker services started"
else
    error "Docker services could not be started.\n         Check: docker compose logs"
fi

# Wait for PostgreSQL to be ready
info "Waiting for PostgreSQL..."
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U qemu -d qemu_interface >> "$LOG_FILE" 2>&1; then
        log "PostgreSQL ready"
        break
    fi
    if [[ $i -eq 30 ]]; then
        error "PostgreSQL not ready after 30 seconds. Check: docker compose logs postgres"
    fi
    sleep 1
done

# ============================================================================
#  Phase 10: Initialize Database
# ============================================================================

header "Phase 10: Initialize Database"

# Schema is automatically created on backend startup (_auto_migrate in main.py).
# Here we wait for the backend to be ready, then run seed.

# Wait for PostgreSQL directly (independent of backend)
info "Waiting for PostgreSQL..."
for i in $(seq 1 60); do
    if docker compose exec -T postgres pg_isready -U qemu -d qemu_interface >> "$LOG_FILE" 2>&1; then
        log "PostgreSQL ready"
        break
    fi
    if [[ $i -eq 60 ]]; then
        warn "PostgreSQL not ready after 60s. Check: docker compose logs postgres"
    fi
    sleep 1
done

# Wait for backend API (including _auto_migrate)
info "Waiting for Backend API..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        log "Backend API ready (schema migration completed)"
        break
    fi
    if [[ $i -eq 60 ]]; then
        warn "Backend API not ready after 60s. Check: docker compose logs backend"
    fi
    sleep 2
done

info "Running database migrations..."
if docker compose exec -T backend alembic upgrade head 2>&1 | tee -a "$LOG_FILE"; then
    log "Alembic migrations completed"
else
    warn "Alembic migration skipped (auto-migration on backend startup will handle it)."
fi

info "Creating admin user and seed data..."
if docker compose exec -T -e ADMIN_PASSWORD="${ADMIN_PASSWORD}" backend python -m scripts.seed 2>&1 | tee -a "$LOG_FILE"; then
    log "Seed data imported"
else
    warn "Seed failed. Can be run manually later (password auto-generated if unset):"
    warn "cd ${INSTALL_DIR} && docker compose exec -T -e ADMIN_PASSWORD=\"${ADMIN_PASSWORD}\" backend python -m scripts.seed"
fi

# ============================================================================
#  Phase 11: Start Host Agent
# ============================================================================

header "Phase 11: Start Host Agent"

# RESTART instead of START – so the agent always loads the latest secrets from .env
run_visible "Restarting qemu-interface-agent..." systemctl restart qemu-interface-agent \
    || warn "Host agent restart returned non-zero – check journalctl -u qemu-interface-agent"

sleep 3
if systemctl is-active --quiet qemu-interface-agent; then
    log "Host Agent running (restarted with current secrets)"
else
    warn "Host Agent could not be started. Check: journalctl -u qemu-interface-agent"
fi

# ============================================================================
#  Phase 12: Check Networking
# ============================================================================

header "Phase 12: Check Networking"

HETZNER_DOCS_URL="https://nycon.github.io/qemu-wi/Hetzner-Dedicated-Bridging/"
HETZNER_DOCS_LOCAL="${INSTALL_DIR}/docs/Hetzner-Dedicated-Bridging.md"
NETWORK_MODE="nat"

# Soft Hetzner detection (hint only – we still ask)
HETZNER_HINT=false
if command -v dmidecode &>/dev/null; then
    _mfr=$(dmidecode -s system-manufacturer 2>/dev/null || true)
    _prod=$(dmidecode -s system-product-name 2>/dev/null || true)
    if echo "${_mfr} ${_prod}" | grep -qiE 'hetzner|Hetzner'; then
        HETZNER_HINT=true
    fi
fi

# Check if br0 already exists
if ip link show br0 &> /dev/null 2>&1; then
    log "Bridge br0 exists – external / public VM IPs are available"
    NETWORK_MODE="bridged"
else
    # Detect primary interface and network config
    DEFAULT_IFACE=$(ip route show default 2>/dev/null | head -1 | awk '{print $5}')
    CURRENT_IP=$(ip -4 addr show "$DEFAULT_IFACE" 2>/dev/null | grep -oP 'inet \K[\d.]+/\d+' | head -1)
    CIDR_MASK="${CURRENT_IP##*/}"
    HOST_IP="${CURRENT_IP%%/*}"

    if [[ "$CIDR_MASK" == "32" ]]; then
        HETZNER_HINT=true
    fi

    info "How should VMs reach the internet?"
    info "  • NAT (default): private IPs + port-forwarding — works everywhere"
    info "  • Bridged (br0): VMs get their own public IPs — needs a real subnet"
    info ""
    if $HETZNER_HINT; then
        info "Hint: this host looks like Hetzner (or uses /32 routing)."
    fi
    info "Hetzner Cloud (/32): br0 on the main NIC usually does NOT work."
    info "Hetzner Dedicated + vSwitch + extra subnet: follow the dedicated guide."
    info "Other providers / lab: br0 on the main NIC is usually fine."
    info ""

    IS_HETZNER=false
    if $HETZNER_HINT; then
        read -r -p "[?] Is this a Hetzner server? [Y/n] " ANSWER_HETZNER
        if [[ -z "$ANSWER_HETZNER" || "$ANSWER_HETZNER" =~ ^[Yy]$ ]]; then
            IS_HETZNER=true
        fi
    else
        read -r -p "[?] Is this a Hetzner server? [y/N] " ANSWER_HETZNER
        if [[ "$ANSWER_HETZNER" =~ ^[Yy]$ ]]; then
            IS_HETZNER=true
        fi
    fi

    if $IS_HETZNER; then
        log "Hetzner selected – skipping automatic br0 on the main NIC"
        info ""
        info "Next steps for Hetzner:"
        info "  1. Finish this install (NAT works immediately)."
        info "  2. For public VM IPs on Dedicated: follow the bridging guide:"
        info "     ${HETZNER_DOCS_URL}"
        if [[ -f "$HETZNER_DOCS_LOCAL" ]]; then
            info "     Local copy: ${HETZNER_DOCS_LOCAL}"
        fi
        info "  3. After br0 exists, add your subnet under Admin → IP Pools (External)."
        info ""
        if [[ "$CIDR_MASK" == "32" ]]; then
            info "Detected /32 – use NAT + port-forwarding unless you add a vSwitch subnet."
        fi
        NETWORK_MODE="nat"
    elif [[ "$CIDR_MASK" == "32" ]]; then
        info "Server uses /32 point-to-point routing."
        info "A host bridge (br0) on this interface is not possible."
        info "NAT networks will be used (port-forwarding for external access)."
        NETWORK_MODE="nat"
    elif [[ -n "$DEFAULT_IFACE" && -n "$HOST_IP" ]]; then
        GATEWAY=$(ip route show default 2>/dev/null | head -1 | awk '{print $3}')
        DNS_SERVERS=$(grep -oP 'nameserver \K[\d.]+' /etc/resolv.conf 2>/dev/null | head -2 | tr '\n' ',' | sed 's/,$//')
        DNS_SERVERS="${DNS_SERVERS:-8.8.8.8,1.1.1.1}"

        info "Detected network: $DEFAULT_IFACE ($CURRENT_IP, gateway $GATEWAY)"
        info ""
        info "Creating br0 lets VMs use public/external IPs like physical machines."
        info "Afterwards add those IPs in the panel: Admin → IP Pools → External."
        info ""

        read -r -p "[?] Create network bridge br0 on $DEFAULT_IFACE? [Y/n] " CREATE_BRIDGE
        if [[ -z "$CREATE_BRIDGE" || "$CREATE_BRIDGE" =~ ^[Yy]$ ]]; then
            # Detect network management system
            if command -v netplan &> /dev/null && [ -d /etc/netplan ]; then
                # --- Netplan (Ubuntu 22.04+, Debian 12+) ---
                log "Creating bridge via Netplan..."

                NETPLAN_FILE="/etc/netplan/90-qemu-bridge.yaml"
                NETPLAN_BACKUP="/etc/netplan/backup-$(date +%Y%m%d%H%M%S)"
                mkdir -p "$NETPLAN_BACKUP"
                cp /etc/netplan/*.yaml "$NETPLAN_BACKUP/" 2>/dev/null || true
                log "Netplan backup saved to $NETPLAN_BACKUP"

                # Disable DHCP/addresses on existing configs for the interface
                for f in /etc/netplan/*.yaml; do
                    [ "$f" = "$NETPLAN_FILE" ] && continue
                    [ -f "$f" ] || continue
                    if grep -q "$DEFAULT_IFACE" "$f" 2>/dev/null; then
                        log "Updating $f – removing $DEFAULT_IFACE config (moved to bridge)"
                        # Comment out the interface block in the old file
                        sed -i "s/^\(\s*\)${DEFAULT_IFACE}:/\1# ${DEFAULT_IFACE}: # moved to bridge/" "$f"
                    fi
                done

                cat > "$NETPLAN_FILE" <<BRIDGEYAML
network:
  version: 2
  renderer: networkd
  ethernets:
    ${DEFAULT_IFACE}:
      dhcp4: false
      dhcp6: false
  bridges:
    br0:
      interfaces:
        - ${DEFAULT_IFACE}
      addresses:
        - ${CURRENT_IP}
      routes:
        - to: default
          via: ${GATEWAY}
      nameservers:
        addresses: [${DNS_SERVERS}]
      parameters:
        stp: false
        forward-delay: 0
BRIDGEYAML

                chmod 600 "$NETPLAN_FILE"

                # Validate before applying
                if netplan generate 2>/dev/null; then
                    log "Netplan config valid. Applying..."
                    # Use try for safety – auto-reverts after 120s if connection lost
                    nohup bash -c "sleep 2 && netplan apply" &>/dev/null &
                    log "Bridge br0 will be active in a few seconds"
                    info "NOTE: If you lose SSH connection, it will recover automatically."
                    info "      Backup: $NETPLAN_BACKUP"
                    sleep 5
                    if ip link show br0 &> /dev/null 2>&1; then
                        log "Bridge br0 created successfully!"
                        NETWORK_MODE="bridged"
                    else
                        warn "Bridge br0 not yet visible – may need a reboot."
                    fi
                else
                    warn "Netplan validation failed! Restoring backup..."
                    cp "$NETPLAN_BACKUP"/*.yaml /etc/netplan/ 2>/dev/null || true
                    rm -f "$NETPLAN_FILE"
                    warn "Bridge not created. NAT networks will be used."
                fi

            elif [ -d /etc/network ] && command -v ifup &> /dev/null; then
                # --- ifupdown (Debian legacy) ---
                log "Creating bridge via /etc/network/interfaces..."

                cp /etc/network/interfaces "/etc/network/interfaces.backup-$(date +%Y%m%d%H%M%S)"

                cat >> /etc/network/interfaces <<IFCFG

# Bridge for QEMU Interface VMs
auto br0
iface br0 inet static
    address ${HOST_IP}
    netmask $(ipcalc -m "$CURRENT_IP" 2>/dev/null | cut -d= -f2 || echo "255.255.255.0")
    gateway ${GATEWAY}
    dns-nameservers ${DNS_SERVERS//,/ }
    bridge_ports ${DEFAULT_IFACE}
    bridge_stp off
    bridge_fd 0
IFCFG

                log "Config written. Activating bridge..."
                nohup bash -c "sleep 2 && ifup br0 2>/dev/null" &>/dev/null &
                sleep 5
                if ip link show br0 &> /dev/null 2>&1; then
                    log "Bridge br0 created successfully!"
                    NETWORK_MODE="bridged"
                else
                    warn "Bridge activation pending – may need a reboot."
                fi

            elif command -v nmcli &> /dev/null; then
                # --- NetworkManager (Fedora, CentOS, RHEL) ---
                log "Creating bridge via NetworkManager..."

                nmcli connection add type bridge ifname br0 con-name br0 \
                    ipv4.method manual ipv4.addresses "$CURRENT_IP" \
                    ipv4.gateway "$GATEWAY" ipv4.dns "${DNS_SERVERS//,/ }" \
                    bridge.stp no 2>/dev/null

                nmcli connection add type bridge-slave ifname "$DEFAULT_IFACE" \
                    master br0 2>/dev/null

                nohup bash -c "sleep 2 && nmcli connection up br0 2>/dev/null" &>/dev/null &
                sleep 5
                if ip link show br0 &> /dev/null 2>&1; then
                    log "Bridge br0 created successfully!"
                    NETWORK_MODE="bridged"
                else
                    warn "Bridge activation pending – may need a reboot."
                fi
            else
                warn "No supported network manager found (netplan/ifupdown/NetworkManager)."
                info "Please create br0 manually. NAT networks will work without it."
            fi
        else
            info "Skipping bridge creation. NAT networks will be used."
            info "You can create br0 later manually if needed."
        fi
    else
        info "Could not detect primary network interface."
        info "NAT networks will work. For bridged networking, create br0 manually."
    fi
fi

if [[ "$NETWORK_MODE" == "bridged" ]]; then
    info "Next: Admin → IP Pools → create an External pool with your public subnet → assign to VMs."
fi

# Always ensure libvirt default NAT network is available
if virsh net-info default &> /dev/null 2>&1; then
    log "libvirt default network available (NAT via virbr0)"
else
    info "Creating libvirt default NAT network..."
    virsh net-define /dev/stdin <<'NETXML'
<network>
  <name>default</name>
  <forward mode='nat'/>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp>
      <range start='192.168.122.2' end='192.168.122.254'/>
    </dhcp>
  </ip>
</network>
NETXML
    virsh net-start default 2>/dev/null || true
    virsh net-autostart default 2>/dev/null || true
    log "libvirt default network created and started"
fi

# --- Host networking for bridged public VMs (Hetzner /29 etc.) ---
info "Configuring kernel networking for bridged VMs..."
cat > /etc/sysctl.d/99-qemu-interface.conf <<'SYSCTL'
# QEMU Interface – bridged VM reachability
net.ipv4.ip_forward = 1
net.ipv4.conf.all.proxy_arp = 1
net.ipv4.conf.default.proxy_arp = 1
SYSCTL
if ip link show br0 &>/dev/null; then
    echo "net.ipv4.conf.br0.proxy_arp = 1" >> /etc/sysctl.d/99-qemu-interface.conf
fi
sysctl --system >> "$LOG_FILE" 2>&1 || sysctl -p /etc/sysctl.d/99-qemu-interface.conf >> "$LOG_FILE" 2>&1 || true
log "Kernel sysctl for bridged VMs applied"

# ============================================================================
#  Phase 13: Configure Firewall + VM Egress Filtering
# ============================================================================

header "Phase 13: Configure Firewall + Egress Filtering"

# --- Host Firewall (incoming ports) ---
# Hard guarantee: iptables DROP for public 8443 (UFW may be inactive)
info "Locking Host Agent port 8443 to localhost + Docker..."
if [[ -x "${INSTALL_DIR}/scripts/lock-agent-port.sh" ]]; then
    bash "${INSTALL_DIR}/scripts/lock-agent-port.sh" 2>&1 | tee -a "$LOG_FILE" || warn "lock-agent-port.sh failed"
    systemctl enable qemu-interface-agent-firewall 2>&1 | tee -a "$LOG_FILE" || true
else
    warn "scripts/lock-agent-port.sh missing – apply 8443 lockdown manually"
fi

if command -v ufw &> /dev/null; then
    info "UFW detected – opening public ports (80/443 only)..."
    # Remove older public agent/VNC rules if present (from previous installs)
    ufw delete allow 8443/tcp 2>&1 | tee -a "$LOG_FILE" || true
    ufw delete allow 6080:6180/tcp 2>&1 | tee -a "$LOG_FILE" || true
    ufw allow 80/tcp comment "QEMU Interface HTTP" 2>&1 | tee -a "$LOG_FILE"
    ufw allow 443/tcp comment "QEMU Interface HTTPS" 2>&1 | tee -a "$LOG_FILE"
    # Host Agent (8443) stays host-local; Docker reaches host.docker.internal
    ufw allow from 127.0.0.1 to any port 8443 proto tcp comment "QEMU Interface Host Agent localhost" 2>&1 | tee -a "$LOG_FILE" || true
    ufw allow from 172.16.0.0/12 to any port 8443 proto tcp comment "QEMU Interface Host Agent Docker" 2>&1 | tee -a "$LOG_FILE" || true
    ufw status verbose 2>&1 | tee -a "$LOG_FILE" || true
    log "UFW rules created (8443 not public)"
elif command -v firewall-cmd &> /dev/null; then
    info "firewalld detected – opening public ports (80/443 only)..."
    firewall-cmd --permanent --remove-port=8443/tcp 2>&1 | tee -a "$LOG_FILE" || true
    firewall-cmd --permanent --remove-port=6080-6180/tcp 2>&1 | tee -a "$LOG_FILE" || true
    firewall-cmd --permanent --add-port=80/tcp 2>&1 | tee -a "$LOG_FILE"
    firewall-cmd --permanent --add-port=443/tcp 2>&1 | tee -a "$LOG_FILE"
    # Explicit rich rules: agent only from localhost + Docker ranges
    firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="127.0.0.0/8" port port="8443" protocol="tcp" accept' 2>&1 | tee -a "$LOG_FILE" || true
    firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="172.16.0.0/12" port port="8443" protocol="tcp" accept' 2>&1 | tee -a "$LOG_FILE" || true
    firewall-cmd --reload 2>&1 | tee -a "$LOG_FILE"
    log "firewalld rules created (8443 not public – Docker uses host gateway)"
else
    warn "No UFW/firewalld – relying on iptables QI_AGENT_INPUT lockdown for 8443."
    warn "Please manually open ports 80 and 443 for the panel."
fi

# --- VM Egress Filtering (outgoing traffic from VMs) ---
info "Setting up VM egress filtering (anti-portscan protection)..."

# Create custom chain for VM egress rules (idempotent)
iptables -N VM_EGRESS 2>/dev/null || true
iptables -F VM_EGRESS 2>/dev/null || true

# Allowed outgoing ports for VMs (whitelist approach)
# DNS
iptables -A VM_EGRESS -p udp --dport 53 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 53 -j ACCEPT
# HTTP / HTTPS
iptables -A VM_EGRESS -p tcp --dport 80 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 443 -j ACCEPT
# SSH
iptables -A VM_EGRESS -p tcp --dport 22 -j ACCEPT
# SMTP / Submission / SMTPS
iptables -A VM_EGRESS -p tcp --dport 25 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 465 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 587 -j ACCEPT
# IMAP / IMAPS
iptables -A VM_EGRESS -p tcp --dport 143 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 993 -j ACCEPT
# POP3 / POP3S
iptables -A VM_EGRESS -p tcp --dport 110 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 995 -j ACCEPT
# NTP
iptables -A VM_EGRESS -p udp --dport 123 -j ACCEPT
# FTP / FTPS
iptables -A VM_EGRESS -p tcp --dport 21 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 990 -j ACCEPT
# MySQL / PostgreSQL / Redis (to external services)
iptables -A VM_EGRESS -p tcp --dport 3306 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 5432 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 6379 -j ACCEPT
# High ports for established connections (e.g. apt/yum repos, API calls)
iptables -A VM_EGRESS -p tcp --dport 8080 -j ACCEPT
iptables -A VM_EGRESS -p tcp --dport 8443 -j ACCEPT
# ICMP (ping)
iptables -A VM_EGRESS -p icmp -j ACCEPT
# Allow already established connections
iptables -A VM_EGRESS -m state --state ESTABLISHED,RELATED -j ACCEPT

# Rate-limit NEW outgoing connections (anti-scan: max 20 new connections/sec per VM)
iptables -A VM_EGRESS -m state --state NEW -m limit --limit 20/sec --limit-burst 40 -j ACCEPT

# Log and DROP everything else (portscans, RDP scans, etc.)
iptables -A VM_EGRESS -m limit --limit 5/min -j LOG --log-prefix "VM_EGRESS_BLOCKED: " --log-level warning
iptables -A VM_EGRESS -j DROP

# Hook VM_EGRESS chain into FORWARD (for VM traffic going to the internet)
# Only apply to traffic from private/VM subnets going outbound
# Remove old rule first (idempotent)
iptables -D FORWARD -s 10.0.0.0/8 -o eth0 -j VM_EGRESS 2>/dev/null || true
iptables -D FORWARD -s 10.0.0.0/8 ! -d 10.0.0.0/8 -j VM_EGRESS 2>/dev/null || true
iptables -D FORWARD -s 192.168.0.0/16 ! -d 192.168.0.0/16 -j VM_EGRESS 2>/dev/null || true
iptables -D FORWARD -s 172.16.0.0/12 ! -d 172.16.0.0/12 -j VM_EGRESS 2>/dev/null || true

# Apply to all private subnets going to public internet
iptables -I FORWARD -s 10.0.0.0/8 ! -d 10.0.0.0/8 -j VM_EGRESS
iptables -I FORWARD -s 192.168.0.0/16 ! -d 192.168.0.0/16 -j VM_EGRESS
iptables -I FORWARD -s 172.16.0.0/12 ! -d 172.16.0.0/12 -j VM_EGRESS

# Allow VM-to-VM traffic (internal networks) without filtering
iptables -I FORWARD -s 10.0.0.0/8 -d 10.0.0.0/8 -j ACCEPT
iptables -I FORWARD -s 192.168.0.0/16 -d 192.168.0.0/16 -j ACCEPT
iptables -I FORWARD -s 172.16.0.0/12 -d 172.16.0.0/12 -j ACCEPT

# Save rules persistently
mkdir -p /etc/iptables
iptables-save > /etc/iptables/rules.v4 2>/dev/null || true

log "VM egress filtering configured"
log "Allowed: DNS, HTTP/S, SSH, SMTP, IMAP, NTP, FTP, MySQL, PostgreSQL"
log "Blocked: RDP (3389), SMB (445), Telnet (23), and all other ports"
log "Rate-limit: Max 20 new outgoing connections/sec per VM (anti-scan)"

# --- Fail2Ban Configuration ---
info "Configuring Fail2Ban (IP blacklist / brute-force protection)..."

# Create log files for fail2ban (must exist before jail starts)
mkdir -p /opt/qemu-interface/logs
touch /opt/qemu-interface/logs/backend-auth.log

# Expose Nginx logs from Docker container to host for fail2ban
# Create log dir and ensure docker writes there
mkdir -p /var/log/qemu-interface/nginx
docker compose logs nginx --no-log-prefix > /var/log/qemu-interface/nginx/access.log 2>/dev/null || true
touch /var/log/qemu-interface/nginx/access.log
touch /var/log/qemu-interface/nginx/error.log

# Determine SSH auth log path (Debian/Ubuntu vs RHEL)
if [[ -f /var/log/auth.log ]]; then
    SSH_LOG_PATH="/var/log/auth.log"
else
    SSH_LOG_PATH="/var/log/secure"
fi

# Create custom jail config (idempotent)
cat > /etc/fail2ban/jail.d/qemu-interface.conf <<F2BCONF
# QEMU Interface – Fail2Ban Configuration

[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
banaction = iptables-multiport

# Protect host SSH
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = ${SSH_LOG_PATH}
maxretry = 5
bantime = 3600

# Brute-force on QEMU Interface API login
[qemu-interface-api]
enabled = true
port = http,https
filter = qemu-interface-api
logpath = /opt/qemu-interface/logs/backend-auth.log
maxretry = 10
findtime = 300
bantime = 1800
F2BCONF

# Create custom filter for API login failures
mkdir -p /etc/fail2ban/filter.d
cat > /etc/fail2ban/filter.d/qemu-interface-api.conf <<'F2BFILTER'
[Definition]
failregex = ^.*LOGIN_FAILED.*from\s+<HOST>.*$
ignoreregex =
F2BFILTER

# Create custom IP blacklist chain (manual bans from UI)
iptables -N IP_BLACKLIST 2>/dev/null || true
iptables -F IP_BLACKLIST 2>/dev/null || true

# Hook blacklist into INPUT chain (idempotent)
iptables -D INPUT -j IP_BLACKLIST 2>/dev/null || true
iptables -I INPUT 1 -j IP_BLACKLIST

# Load persistent blacklist if exists (DROP rules first, RETURN at end)
BLACKLIST_FILE="/etc/qemu-interface/ip-blacklist.json"
if [[ -f "$BLACKLIST_FILE" ]]; then
    python3 -c "
import json, subprocess
try:
    with open('$BLACKLIST_FILE') as f:
        data = json.load(f)
    for entry in data.get('blocked_ips', []):
        ip = entry.get('ip', '')
        if ip:
            subprocess.run(['iptables', '-A', 'IP_BLACKLIST', '-s', ip, '-j', 'DROP'], capture_output=True)
    print(f'Loaded {len(data.get(\"blocked_ips\", []))} blacklisted IPs')
except Exception as e:
    print(f'No blacklist to load: {e}')
" 2>/dev/null || true
fi

# RETURN must be the LAST rule so DROP rules above it are evaluated first
iptables -A IP_BLACKLIST -j RETURN

iptables-save > /etc/iptables/rules.v4 2>/dev/null || true

# Enable and start fail2ban
run_visible "Enabling fail2ban..." systemctl enable fail2ban || true
run_visible "Restarting fail2ban..." systemctl restart fail2ban || true
log "Fail2Ban configured and started"
log "SSH protection: 5 failed attempts → 1 hour ban"
log "API protection: 10 failed attempts → 30 min ban"

# ============================================================================
#  Phase 14: Status Check
# ============================================================================

header "Phase 14: Status Check"

echo ""
echo -e "${BOLD}Docker Containers:${NC}"
cd "${INSTALL_DIR}" && docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null \
    || docker compose ps

echo ""
echo -e "${BOLD}Host Agent:${NC}"
systemctl status qemu-interface-agent --no-pager -l 2>/dev/null | head -5 || true

echo ""

# Backend Health Check
info "Checking Backend health..."
sleep 3
for i in $(seq 1 10); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        log "Backend API reachable"
        break
    fi
    if [[ $i -eq 10 ]]; then
        warn "Backend API not responding yet. Wait a moment and check: curl http://localhost:8000/health"
    fi
    sleep 2
done

# ============================================================================
#  Summary
# ============================================================================

echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${GREEN}  ✓ Installation completed successfully!${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${BOLD}Open in browser:${NC}"
echo ""
if [[ "$SSL_FOUND" == "true" ]]; then
    echo -e "  ${BOLD}${CYAN}https://${SSL_DOMAIN:-$SERVER_IP}${NC}"
else
    echo -e "  ${BOLD}${CYAN}http://${SERVER_IP}${NC}"
fi
echo ""
echo -e "${BOLD}Login credentials:${NC}"
echo -e "  Username:  ${CYAN}admin${NC}"
echo -e "  Password:  ${CYAN}${ADMIN_PASSWORD}${NC}"
echo ""
echo -e "${BOLD}Automatically configured:${NC}"
echo -e "  ${GREEN}✓${NC} All secrets & tokens (JWT, DB, Agent)"
echo -e "  ${GREEN}✓${NC} PostgreSQL + Redis"
echo -e "  ${GREEN}✓${NC} Backend API + Celery Worker (with scheduler)"
echo -e "  ${GREEN}✓${NC} Frontend (React)"
echo -e "  ${GREEN}✓${NC} Nginx Reverse Proxy"
if [[ "$SSL_FOUND" == "true" ]]; then
    echo -e "  ${GREEN}✓${NC} SSL/TLS (HTTPS enabled)"
else
    echo -e "  ${YELLOW}○${NC} SSL/TLS (not configured – HTTP-only)"
fi
echo -e "  ${GREEN}✓${NC} Host Agent (KVM/libvirt)"
echo -e "  ${GREEN}✓${NC} TLS certificates (Host Agent internal)"
echo -e "  ${GREEN}✓${NC} Firewall rules"
if [[ "${NETWORK_MODE:-nat}" == "bridged" ]]; then
    echo -e "  ${GREEN}✓${NC} Bridge br0 (public/external VM IPs)"
else
    echo -e "  ${YELLOW}○${NC} Bridge br0 (NAT mode – add later if needed)"
fi
echo ""
echo -e "${BOLD}Networking next steps:${NC}"
if [[ "${NETWORK_MODE:-nat}" == "bridged" ]]; then
    echo -e "  → Admin → IP Pools → External: enter your public subnet, gateway, bridge=br0"
else
    echo -e "  → NAT works now (port-forwarding)."
    echo -e "  → Hetzner public IPs: ${CYAN}https://nycon.github.io/qemu-wi/Hetzner-Dedicated-Bridging/${NC}"
fi
echo ""
echo -e "${BOLD}Features:${NC}"
echo -e "  • VM management (create, start, stop, clone)"
echo -e "  • VM templates / presets"
echo -e "  • VM import/export"
echo -e "  • Disk management (create, attach, resize)"
echo -e "  • Scheduled backups with retention"
echo -e "  • Email notifications"
echo -e "  • VNC console in browser"
echo -e "  • Networks, port-forwarding, reverse proxy"
echo -e "  • Multi-tenant, RBAC, 2FA"
echo ""
echo -e "${BOLD}Next steps in the web interface:${NC}"
if [[ "$SSL_FOUND" != "true" ]]; then
    echo -e "  1. ${YELLOW}Set up SSL certificate:${NC}"
    echo -e "     ${CYAN}certbot certonly --webroot -w /var/lib/letsencrypt -d YOUR-DOMAIN${NC}"
    echo -e "     Then: ${CYAN}cd ${INSTALL_DIR} && bash install.sh${NC} (auto-detects certificate)"
    echo -e "  2. Log in and change password"
else
    echo -e "  1. Log in and change password"
fi
echo -e "  3. Create a network (Networks → + Create Network)"
echo -e "  4. Download a cloud image (Admin → Cloud Images)"
echo -e "  5. Create your first VM"
echo ""
echo -e "${BOLD}Secrets saved:${NC}      ${CONFIG_DIR}/secrets"
echo -e "${BOLD}Installation log:${NC}   ${LOG_FILE}"
echo ""
echo -e "${BOLD}Useful commands:${NC}"
echo -e "  ${CYAN}cd ${INSTALL_DIR} && docker compose ps${NC}        # Container status"
echo -e "  ${CYAN}cd ${INSTALL_DIR} && docker compose logs -f${NC}   # View logs"
echo -e "  ${CYAN}systemctl status qemu-interface-agent${NC}         # Host Agent status"
echo -e "  ${CYAN}journalctl -u qemu-interface-agent -f${NC}         # Agent logs"
echo ""
echo -e "${BOLD}Update:${NC}"
echo -e "  ${CYAN}cd ${INSTALL_DIR} && git pull && sudo bash install.sh${NC}"
echo -e "  ${CYAN}# optional kurz: sudo bash install.sh --update${NC}"
echo ""
echo -e "${BOLD}${GREEN}Enjoy the QEMU Interface Panel!${NC}"
echo ""
