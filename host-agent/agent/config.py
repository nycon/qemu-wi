"""
Host Agent configuration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Agent — bind address is set by systemd/install.sh.
    # Prefer localhost; if Docker uses host-gateway, install binds 0.0.0.0 and
    # scripts/lock-agent-port.sh drops public ingress to AGENT_PORT.
    AGENT_HOST: str = "127.0.0.1"
    AGENT_PORT: int = 8443
    AGENT_TOKEN: str = "CHANGE-ME-AGENT-TOKEN"

    # JWT (for console token verification – must match backend JWT_SECRET_KEY)
    JWT_SECRET_KEY: str = "change-me-jwt-secret"

    # TLS
    TLS_CERT_FILE: str = "/etc/qemu-interface/agent-cert.pem"
    TLS_KEY_FILE: str = "/etc/qemu-interface/agent-key.pem"

    # Libvirt
    LIBVIRT_URI: str = "qemu:///system"

    # Storage paths (backups are stored under STORAGE_BASE_PATH/backups)
    STORAGE_BASE_PATH: str = "/var/lib/qemu-interface/storage"
    CLOUD_IMAGES_PATH: str = "/var/lib/qemu-interface/images"
    CLOUD_INIT_PATH: str = "/var/lib/qemu-interface/cloud-init"

    # Network
    DEFAULT_BRIDGE: str = "br0"

    # VNC — bind to localhost only; panel reaches it via websockify on the host.
    VNC_LISTEN_ADDRESS: str = "127.0.0.1"
    VNC_BASE_PORT: int = 5900
    VNC_WEBSOCKET_BASE_PORT: int = 6080

    # Control Plane callback
    CONTROL_PLANE_URL: str = "http://localhost:8000"
    CONTROL_PLANE_TOKEN: str = ""


agent_settings = AgentSettings()
