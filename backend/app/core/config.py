"""
Application configuration using pydantic-settings.
All settings are loaded from environment variables with sensible defaults.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    APP_NAME: str = "QEMU Interface"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION-USE-OPENSSL-RAND-HEX-64"

    # API
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_HOSTS: list[str] = ["*"]
    # Public self-registration (POST /auth/register). Keep False for production.
    ALLOW_PUBLIC_REGISTRATION: bool = False
    # Expose /api/docs and /api/openapi.json without auth (also enabled when DEBUG=true).
    EXPOSE_API_DOCS: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://qemu:qemu@localhost:5432/qemu_interface"
    DATABASE_ECHO: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # JWT Auth
    JWT_SECRET_KEY: str = "CHANGE-ME-JWT-SECRET"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Host Agent
    HOST_AGENT_URL: str = "https://localhost:8443"
    HOST_AGENT_TOKEN: str = "CHANGE-ME-AGENT-TOKEN"

    # Storage
    DEFAULT_STORAGE_POOL_PATH: str = "/var/lib/qemu-interface/storage"
    CLOUD_IMAGES_PATH: str = "/var/lib/qemu-interface/images"
    CLOUD_INIT_PATH: str = "/var/lib/qemu-interface/cloud-init"

    # VNC / Console
    VNC_WEBSOCKET_HOST: str = "0.0.0.0"
    VNC_WEBSOCKET_PORT_RANGE_START: int = 6080
    VNC_WEBSOCKET_PORT_RANGE_END: int = 6180
    CONSOLE_TOKEN_EXPIRE_SECONDS: int = 120

    # Networking
    DEFAULT_BRIDGE: str = "br0"
    EXTERNAL_VM_MTU: int = 1400

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Email / SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "QEMU Interface"
    SMTP_USE_TLS: bool = True
    SMTP_ALLOW_INVALID_CERT: bool = False
    SMTP_ENABLED: bool = False


settings = Settings()
