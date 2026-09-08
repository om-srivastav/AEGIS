from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

from app.providers.model_contracts import Limits


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(repr=False, compare=False)
    limits: Limits = field(default_factory=Limits)

    def public_metadata(self) -> dict:
        return {
            "provider": self.provider,
            "requested_model": self.model,
            "limits": {item.name: getattr(self.limits, item.name) for item in fields(Limits)},
        }


def read_model_config(prefix: str) -> ModelConfig:
    provider = os.environ.get(f"{prefix}_PROVIDER", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    api_key = os.environ.get(f"{prefix}_API_KEY", "")
    if not provider or not model or not api_key:
        raise ValueError(f"incomplete_model_configuration:{prefix}")
    if provider != "anthropic":
        raise ValueError(f"unsupported_provider:{prefix}")

    values = {}
    defaults = Limits()
    for item in fields(Limits):
        name = f"{prefix}_{item.name.upper()}"
        raw = os.environ.get(name)
        if raw is None:
            values[item.name] = getattr(defaults, item.name)
        elif item.name in ("wall_seconds", "request_seconds"):
            values[item.name] = float(raw)
        else:
            values[item.name] = int(raw)
    return ModelConfig(provider, model, api_key, Limits(**values))


@dataclass(frozen=True)
class ModelSettings:
    agent_adapter: str
    reasoning_provider: str
    agent: ModelConfig | None
    diagnostic: ModelConfig | None


def read_settings() -> ModelSettings:
    agent_adapter = os.environ.get("AEGIS_AGENT_ADAPTER", "deterministic").strip()
    reasoning_provider = os.environ.get("AEGIS_REASONING_PROVIDER", "deterministic").strip()
    if agent_adapter not in ("deterministic", "model"):
        raise ValueError("unsupported_agent_adapter")
    if reasoning_provider not in ("deterministic", "model"):
        raise ValueError("unsupported_reasoning_provider")
    return ModelSettings(
        agent_adapter=agent_adapter,
        reasoning_provider=reasoning_provider,
        agent=read_model_config("AEGIS_AUT") if agent_adapter == "model" else None,
        diagnostic=read_model_config("AEGIS_DIAG") if reasoning_provider == "model" else None,
    )


def build_transport(config: ModelConfig):
    if config.provider == "anthropic":
        from app.providers.anthropic_transport import AnthropicTransport
        return AnthropicTransport(config.api_key)
    raise ValueError("unsupported_provider")
