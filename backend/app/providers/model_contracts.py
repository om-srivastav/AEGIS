from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from jsonschema import Draft202012Validator, validators

FailureKind = Literal["model", "tool", "policy_controller", "provider_harness"]
FinishReason = Literal["stop", "tools", "length", "refusal", "unsupported"]


class Fault(Exception):
    def __init__(self, kind: FailureKind, code: str):
        super().__init__(code)
        self.kind = kind
        self.code = code


@dataclass(frozen=True)
class Text:
    text: str
    kind: Literal["text"] = field(default="text", init=False)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any
    kind: Literal["tool_call"] = field(default="tool_call", init=False)


@dataclass(frozen=True)
class ToolResult:
    id: str
    data: dict[str, Any]
    is_error: bool = False
    kind: Literal["tool_result"] = field(default="tool_result", init=False)


Part = Text | ToolCall | ToolResult


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    parts: tuple[Part, ...]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_output_tokens: int
    output_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelResponse:
    message: Message
    finish: FinishReason
    input_tokens: int
    output_tokens: int
    resolved_model: str
    response_id: str
    request_id: str | None = None


class ModelTransport(Protocol):
    provider: str

    async def count_tokens(self, request: ModelRequest, timeout: float, audit: "Audit") -> int: ...

    async def complete(self, request: ModelRequest, timeout: float, audit: "Audit") -> ModelResponse: ...


@dataclass(frozen=True)
class Limits:
    max_steps: int = 12
    max_tool_calls: int = 10
    max_provider_requests: int = 24
    max_total_tokens: int = 32_768
    max_output_tokens: int = 2_048
    max_request_bytes: int = 131_072
    max_tool_result_bytes: int = 65_536
    token_reserve: int = 512
    wall_seconds: float = 90.0
    request_seconds: float = 30.0

    def __post_init__(self) -> None:
        integer_fields = (
            "max_steps",
            "max_tool_calls",
            "max_provider_requests",
            "max_total_tokens",
            "max_output_tokens",
            "max_request_bytes",
            "max_tool_result_bytes",
            "token_reserve",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"invalid_limit:{name}")
        for name in ("wall_seconds", "request_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid_limit:{name}")


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def strict_json(text: str, max_bytes: int = 262_144) -> Any:
    if len(text.encode("utf-8")) > max_bytes:
        raise ValueError("json_too_large")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError("non_finite_json_number")

    return json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


_type_checker = (
    Draft202012Validator.TYPE_CHECKER
    .redefine("integer", lambda checker, value: type(value) is int)
    .redefine(
        "number",
        lambda checker, value: type(value) in (int, float)
        and (type(value) is int or math.isfinite(value)),
    )
)
StrictValidator = validators.extend(Draft202012Validator, type_checker=_type_checker)


def closed_validator(schema: dict[str, Any]) -> Any:
    """Compile trusted local-only JSON schemas with closed object boundaries."""

    def inspect(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in node:
                    ref = node[key]
                    if key != "$ref" or not isinstance(ref, str) or not ref.startswith("#/"):
                        raise ValueError("external_or_dynamic_schema_reference")
            value_type = node.get("type")
            object_type = value_type == "object" or (
                isinstance(value_type, list) and "object" in value_type
            )
            if object_type or "properties" in node:
                if node.get("additionalProperties") is not False:
                    raise ValueError("schema_object_must_be_closed")
            for value in node.values():
                inspect(value)
        elif isinstance(node, list):
            for value in node:
                inspect(value)

    inspect(schema)
    StrictValidator.check_schema(schema)
    return StrictValidator(schema)


class Audit:
    """Adapter that writes model/provider events into the existing trace boundary."""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None], secrets: tuple[str, ...] = ()):
        self._emit = emit
        self._secrets = tuple(secret for secret in secrets if secret)

    def __call__(self, event: str, **payload: Any) -> None:
        def scrub(value: Any) -> Any:
            if isinstance(value, str):
                for secret in self._secrets:
                    value = value.replace(secret, "[REDACTED]")
                return value
            if isinstance(value, list):
                return [scrub(item) for item in value]
            if isinstance(value, dict):
                return {scrub(key): scrub(item) for key, item in value.items()}
            return value

        try:
            frozen = json.loads(json_bytes(payload))
            self._emit(event, scrub(frozen))
        except Fault:
            raise
        except Exception:
            raise Fault("provider_harness", "trace_write_failed") from None
