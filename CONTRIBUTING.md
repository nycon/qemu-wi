# Contributing

Thanks for contributing to QEMU Interface / qemu-wi.

## Development

See [`docs/Development.md`](docs/Development.md) for local setup.

## Pull requests

- Keep changes focused and documented when they affect install, security, or networking defaults.
- Do not commit secrets (`.env`, keys, certs, `docker-compose.override.yml`).
- Prefer `sudo bash install.sh` as the supported production path; avoid teaching insecure Compose defaults.

## Security

Report vulnerabilities privately – see [`SECURITY.md`](SECURITY.md).
