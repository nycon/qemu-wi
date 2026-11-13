"""
Shared SSL verification for Host Agent connections.

The agent uses a self-signed TLS certificate. In production the cert
is mounted into the container at /app/agent-ca.pem.  When the file
exists we verify against it; otherwise we fall back to unverified
(development only).
"""

import os

_AGENT_CA_PATH = "/app/agent-ca.pem"

AGENT_SSL_VERIFY: str | bool = (
    _AGENT_CA_PATH if os.path.isfile(_AGENT_CA_PATH) else False
)
