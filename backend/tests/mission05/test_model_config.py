import os

import pytest

from app.providers.model_config import read_settings


def clear(monkeypatch):
    for name in list(os.environ):
        if name.startswith("AEGIS_"):
            monkeypatch.delenv(name, raising=False)


def test_default_configuration_needs_no_api_credentials(monkeypatch):
    clear(monkeypatch)
    settings = read_settings()
    assert settings.agent_adapter == "deterministic"
    assert settings.reasoning_provider == "deterministic"
    assert settings.agent is None
    assert settings.diagnostic is None


def test_agent_and_diagnostic_roles_are_independently_configured(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("AEGIS_AGENT_ADAPTER", "model")
    monkeypatch.setenv("AEGIS_REASONING_PROVIDER", "model")
    monkeypatch.setenv("AEGIS_AUT_PROVIDER", "anthropic")
    monkeypatch.setenv("AEGIS_AUT_MODEL", "agent-model")
    monkeypatch.setenv("AEGIS_AUT_API_KEY", "agent-secret")
    monkeypatch.setenv("AEGIS_DIAG_PROVIDER", "anthropic")
    monkeypatch.setenv("AEGIS_DIAG_MODEL", "diag-model")
    monkeypatch.setenv("AEGIS_DIAG_API_KEY", "diag-secret")
    settings = read_settings()
    assert settings.agent.model == "agent-model"
    assert settings.diagnostic.model == "diag-model"
    assert settings.agent.api_key != settings.diagnostic.api_key
    assert "agent-secret" not in repr(settings.agent)
    assert "diag-secret" not in repr(settings.diagnostic)


def test_enabled_model_role_requires_complete_config(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("AEGIS_AGENT_ADAPTER", "model")
    with pytest.raises(ValueError, match="incomplete_model_configuration"):
        read_settings()
