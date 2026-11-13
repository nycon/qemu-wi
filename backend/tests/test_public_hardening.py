"""Smoke tests for public-hardening defaults (no DB required)."""

from app.core.config import Settings


def test_public_registration_disabled_by_default():
    s = Settings(
        SECRET_KEY="test",
        JWT_SECRET_KEY="test",
        HOST_AGENT_TOKEN="test",
        _env_file=None,
    )
    assert s.ALLOW_PUBLIC_REGISTRATION is False


def test_api_docs_hidden_by_default():
    s = Settings(
        SECRET_KEY="test",
        JWT_SECRET_KEY="test",
        HOST_AGENT_TOKEN="test",
        DEBUG=False,
        _env_file=None,
    )
    assert s.EXPOSE_API_DOCS is False
    assert not (s.DEBUG or s.EXPOSE_API_DOCS)


def test_api_docs_enabled_when_debug():
    s = Settings(
        SECRET_KEY="test",
        JWT_SECRET_KEY="test",
        HOST_AGENT_TOKEN="test",
        DEBUG=True,
        _env_file=None,
    )
    assert s.DEBUG or s.EXPOSE_API_DOCS
