"""
Nginx platform server config (HTTP/HTTPS) written outside the git-tracked nginx.conf.

Git pull updates deploy/nginx/nginx.conf safely; host-specific SSL lives in
/etc/qemu-interface/nginx-platform.conf only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

NGINX_PLATFORM_CONF = "/etc/qemu-interface/nginx-platform.conf"
SSL_CONFIG_FILE = "/opt/qemu-interface/config/ssl.json"
def _platform_http_template() -> Path:
    for candidate in (
        Path("/opt/qemu-interface/deploy/nginx/platform-http.conf"),
        Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "platform-http.conf",
    ):
        if candidate.is_file():
            return candidate
    return Path("/opt/qemu-interface/deploy/nginx/platform-http.conf")


def qemu_location_blocks() -> str:
    return """
        # Block common scanner paths
        location ~ /\\.(git|env|svn|hg) {
            return 404;
            access_log off;
        }
        location ~ ^/(\\.env|cgi-bin/) {
            return 404;
            access_log off;
        }

        location /api/ {
            limit_req zone=api burst=50 nodelay;
            set $backend_url http://backend:8000;
            proxy_pass $backend_url;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 86400;
        }

        location /api/v1/auth/ {
            limit_req zone=auth burst=10 nodelay;
            set $backend_url http://backend:8000;
            proxy_pass $backend_url;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /health {
            set $backend_url http://backend:8000;
            proxy_pass $backend_url;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        location /websockify {
            proxy_pass https://host.docker.internal:8443/websockify;
            proxy_ssl_verify off;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 86400;
            proxy_send_timeout 86400;
        }

        location / {
            set $frontend_url http://frontend:80;
            proxy_pass $frontend_url;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
"""


def render_http_platform() -> str:
    loc = qemu_location_blocks()
    return f"""    server {{
        listen 80;
        server_name _;

        location /.well-known/acme-challenge/ {{
            root /var/lib/letsencrypt;
        }}
{loc}
    }}
"""


def render_https_platform(domain: str, cert_path: str, key_path: str) -> str:
    loc = qemu_location_blocks()
    return f"""    server {{
        listen 80;
        server_name {domain} _;

        location /.well-known/acme-challenge/ {{
            root /var/lib/letsencrypt;
        }}

        location / {{
            return 301 https://$host$request_uri;
        }}
    }}

    server {{
        listen 443 ssl http2;
        server_name {domain} _;

        ssl_certificate     {cert_path};
        ssl_certificate_key {key_path};
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;
        ssl_session_cache   shared:SSL:10m;
        ssl_session_timeout 10m;

        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
{loc}
    }}
"""


def load_ssl_config() -> dict:
    if os.path.exists(SSL_CONFIG_FILE):
        try:
            with open(SSL_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"domain": "", "ssl_enabled": False, "cert_path": "", "key_path": ""}


def render_platform_from_ssl(ssl_config: dict | None = None) -> str:
    cfg = ssl_config or load_ssl_config()
    if (
        cfg.get("ssl_enabled")
        and cfg.get("domain")
        and cfg.get("cert_path")
        and cfg.get("key_path")
        and os.path.exists(cfg["cert_path"])
        and os.path.exists(cfg["key_path"])
    ):
        return render_https_platform(cfg["domain"], cfg["cert_path"], cfg["key_path"])
    template = _platform_http_template()
    if template.is_file():
        return template.read_text()
    return render_http_platform()


def write_platform_config(
    path: str = NGINX_PLATFORM_CONF,
    ssl_config: dict | None = None,
) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    content = render_platform_from_ssl(ssl_config)
    with open(path, "w") as f:
        f.write(content)
    return content


def sync_platform_config() -> None:
    """Idempotent: regenerate platform nginx servers from ssl.json."""
    write_platform_config()


def lock_cors_to_panel_domain(domain: str) -> list[str]:
    """
    When the admin defines the panel domain (SSL setup), lock backend CORS
    and ALLOWED_HOSTS to that hostname — same as the live public go-live patch.
    Returns human-readable step lines (for SSL setup response).
    """
    import re
    import subprocess

    domain = (domain or "").strip().lower().rstrip(".")
    steps: list[str] = []
    if not domain or domain == "localhost" or domain == "*":
        return steps

    cors = f'["https://{domain}"]'
    hosts = f'["{domain}","localhost","127.0.0.1"]'
    install_dir = "/opt/qemu-interface"
    env_path = f"{install_dir}/backend/.env"
    override_path = f"{install_dir}/docker-compose.override.yml"
    secrets_path = "/etc/qemu-interface/secrets"

    def _set_env_line(path: str, key: str, value: str) -> None:
        if not os.path.exists(path):
            return
        lines = open(path).read().splitlines()
        found = False
        out = []
        for line in lines:
            if line.startswith(f"{key}="):
                out.append(f"{key}={value}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{key}={value}")
        with open(path, "w") as fh:
            fh.write("\n".join(out) + "\n")

    if os.path.exists(env_path):
        _set_env_line(env_path, "CORS_ORIGINS", cors)
        _set_env_line(env_path, "ALLOWED_HOSTS", hosts)
        _set_env_line(env_path, "PANEL_DOMAIN", domain)
        steps.append(f"backend/.env CORS/ALLOWED_HOSTS → {domain}")

    if os.path.exists(override_path):
        text = open(override_path).read()
        cors_y = f"'[\"https://{domain}\"]'"
        hosts_y = f"'[\"{domain}\",\"localhost\",\"127.0.0.1\"]'"
        text = re.sub(r"CORS_ORIGINS:\s*'?\[[^\]]*\]'?", f"CORS_ORIGINS: {cors_y}", text)
        text = re.sub(r"ALLOWED_HOSTS:\s*'?\[[^\]]*\]'?", f"ALLOWED_HOSTS: {hosts_y}", text)
        if re.search(r"PANEL_DOMAIN:", text):
            text = re.sub(r'PANEL_DOMAIN:\s*"?[^"\n]*"?', f'PANEL_DOMAIN: "{domain}"', text)
        else:
            text = text.replace(
                "ALLOW_PUBLIC_REGISTRATION:",
                f'PANEL_DOMAIN: "{domain}"\n      ALLOW_PUBLIC_REGISTRATION:',
                1,
            )
        with open(override_path, "w") as fh:
            fh.write(text)
        steps.append(f"docker-compose.override.yml CORS/ALLOWED_HOSTS → {domain}")

    if os.path.exists(secrets_path):
        _set_env_line(secrets_path, "PANEL_DOMAIN", domain)

    # Recreate API so TrustedHost/CORS pick up new env
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "backend", "--force-recreate"],
            cwd=install_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        steps.append("backend recreated with locked CORS/hosts")
    except Exception as e:
        steps.append(f"backend recreate skipped: {e}")

    return steps
