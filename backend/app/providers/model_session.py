from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, replace
from typing import Any, Awaitable, Callable

from app.providers.model_contracts import (
    Audit,
    Fault,
    Limits,
    Message,
    ModelRequest,
    ModelResponse,
    ModelTransport,
    ToolSpec,
    json_bytes,
)


class Meter:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.started = time.monotonic()
        self.deadline = self.started + limits.wall_seconds
        self.steps = 0
        self.tool_calls = 0
        self.provider_requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_unknown = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Fault("policy_controller", "wall_time_limit")
        return remaining

    def reserve_step(self) -> None:
        self.remaining()
        if self.steps >= self.limits.max_steps:
            raise Fault("policy_controller", "step_limit")
        self.steps += 1

    def reserve_tool(self) -> None:
        self.remaining()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise Fault("policy_controller", "tool_call_limit")
        self.tool_calls += 1

    def reserve_provider_request(self) -> None:
        self.remaining()
        if self.provider_requests >= self.limits.max_provider_requests:
            raise Fault("policy_controller", "provider_request_limit")
        self.provider_requests += 1

    def charge(self, response: ModelResponse) -> None:
        for value in (response.input_tokens, response.output_tokens):
            if type(value) is not int or value < 0:
                self.usage_unknown = True
                raise Fault("provider_harness", "invalid_provider_usage")
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        if self.total_tokens > self.limits.max_total_tokens:
            raise Fault("policy_controller", "total_token_limit")

    def snapshot(self) -> dict[str, Any]:
        return {
            "model_steps": self.steps,
            "tool_calls": self.tool_calls,
            "provider_requests": self.provider_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "usage_unknown": self.usage_unknown,
            "elapsed_seconds": time.monotonic() - self.started,
        }


class ModelSession:
    def __init__(self, transport: ModelTransport, model: str, limits: Limits, audit: Audit):
        self.transport = transport
        self.model = model
        self.meter = Meter(limits)
        self.audit = audit

    async def _rpc(self, operation: str, invoke: Callable[[float], Awaitable[Any]]) -> Any:
        self.meter.reserve_provider_request()
        timeout = min(self.meter.remaining(), self.meter.limits.request_seconds)
        self.audit(
            "provider.operation.started",
            operation=operation,
            request_number=self.meter.provider_requests,
        )
        try:
            async with asyncio.timeout(timeout):
                result = await invoke(timeout)
            self.meter.remaining()
            return result
        except asyncio.CancelledError:
            if operation == "generate":
                self.meter.usage_unknown = True
            self.audit("provider.operation.cancelled", operation=operation)
            raise
        except TimeoutError:
            if operation == "generate":
                self.meter.usage_unknown = True
            failure = (
                Fault("policy_controller", "wall_time_limit")
                if time.monotonic() >= self.meter.deadline
                else Fault("provider_harness", "provider_request_timeout")
            )
            self.audit(
                "provider.operation.failed",
                operation=operation,
                failure_kind=failure.kind,
                failure_code=failure.code,
            )
            raise failure from None
        except Fault as exc:
            if operation == "generate":
                self.meter.usage_unknown = True
            self.audit(
                "provider.operation.failed",
                operation=operation,
                failure_kind=exc.kind,
                failure_code=exc.code,
            )
            raise
        except Exception:
            if operation == "generate":
                self.meter.usage_unknown = True
            self.audit(
                "provider.operation.failed",
                operation=operation,
                failure_kind="provider_harness",
                failure_code="unexpected_provider_error",
            )
            raise Fault("provider_harness", "unexpected_provider_error") from None

    async def turn(
        self,
        *,
        system: str,
        messages: tuple[Message, ...],
        tools: tuple[ToolSpec, ...] = (),
        output_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        self.meter.reserve_step()
        limits = self.meter.limits
        if self.meter.provider_requests + 2 > limits.max_provider_requests:
            raise Fault("policy_controller", "provider_request_limit")

        request = ModelRequest(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools,
            max_output_tokens=limits.max_output_tokens,
            output_schema=output_schema,
        )
        if len(json_bytes(asdict(request))) > limits.max_request_bytes:
            raise Fault("policy_controller", "request_size_limit")

        self.audit("model.request.prepared", request=asdict(request))
        estimated_input = await self._rpc(
            "count_tokens",
            lambda timeout: self.transport.count_tokens(request, timeout, self.audit),
        )
        if type(estimated_input) is not int or estimated_input < 0:
            raise Fault("provider_harness", "invalid_token_count")

        available_output = (
            limits.max_total_tokens
            - self.meter.total_tokens
            - estimated_input
            - limits.token_reserve
        )
        if available_output < 1:
            raise Fault("policy_controller", "token_admission_limit")

        request = replace(
            request,
            max_output_tokens=min(limits.max_output_tokens, available_output),
        )
        self.audit(
            "model.request",
            request=asdict(request),
            estimated_input_tokens=estimated_input,
            token_reserve=limits.token_reserve,
        )
        response = await self._rpc(
            "generate",
            lambda timeout: self.transport.complete(request, timeout, self.audit),
        )
        self.audit("model.response", response=asdict(response))
        self.meter.charge(response)

        if response.output_tokens > request.max_output_tokens:
            raise Fault("provider_harness", "provider_output_limit_violation")
        if response.message.role != "assistant":
            raise Fault("provider_harness", "invalid_response_role")
        return response
