# Security Policy

## Supported versions

Security fixes are applied on the `main` branch of this repository.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately instead:

1. Prefer **GitHub Security Advisories** for this repository (Security → Advisories → New draft advisory), or
2. Contact the maintainers via the repository owner’s GitHub profile / email listed there.

Include:

- Affected version / commit
- Reproduction steps (PoC if possible)
- Impact assessment (auth bypass, RCE, data exposure, etc.)

We aim to acknowledge reports within a few days and coordinate a fix before any public disclosure.

## Safe deployment notes

- Prefer **`sudo bash install.sh`** for production. It generates secrets and does **not** open Host Agent port **8443** to the public internet.
- Do **not** put a raw `docker compose up` with repository default secrets on the public internet.
- Enable HTTPS (Let’s Encrypt or custom certificates) before exposing a panel to the internet.
- Keep `ALLOW_PUBLIC_REGISTRATION=false` and `EXPOSE_API_DOCS=false` unless you intentionally need them.
- Enable **2FA** for administrator accounts.

Product hardening details: [`docs/Security.md`](docs/Security.md).
