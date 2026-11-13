#!/usr/bin/env bash
# Restrict Host Agent port 8443 to localhost + Docker bridge ranges.
# Docker reaches the agent via host.docker.internal → host-gateway (172.16/12),
# so the process may still bind 0.0.0.0; public ingress is dropped here.
set -euo pipefail

PORT="${AGENT_PORT:-8443}"
CHAIN="QI_AGENT_INPUT"

iptables -N "$CHAIN" 2>/dev/null || true
iptables -F "$CHAIN"

# Local processes / health checks
iptables -A "$CHAIN" -s 127.0.0.0/8 -j ACCEPT
iptables -A "$CHAIN" -i lo -j ACCEPT
# Docker bridge / compose networks (host-gateway)
iptables -A "$CHAIN" -s 172.16.0.0/12 -j ACCEPT
# Drop traffic arriving on the primary public NIC (defense in depth)
PRIMARY_IF=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}' || true)
if [[ -n "${PRIMARY_IF}" && "${PRIMARY_IF}" != "lo" ]]; then
  iptables -A "$CHAIN" -i "${PRIMARY_IF}" -j DROP
fi
# Drop everything else (public internet, VMs, etc.)
iptables -A "$CHAIN" -j DROP

# Ensure jump exists at the top of INPUT (idempotent)
while iptables -C INPUT -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null; do
  iptables -D INPUT -p tcp --dport "$PORT" -j "$CHAIN" || break
done
iptables -I INPUT 1 -p tcp --dport "$PORT" -j "$CHAIN"

# Persist if helpers exist
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save >/dev/null 2>&1 || true
elif [[ -d /etc/iptables ]]; then
  mkdir -p /etc/iptables
  iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
fi

echo "Host Agent port ${PORT} locked to localhost + Docker (172.16.0.0/12)."
