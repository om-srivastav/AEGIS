from __future__ import annotations

from typing import Any

import httpx

from app.providers.model_contracts import (
    Audit,
    Fault,
    Message,
    ModelRequest,
    ModelResponse,
    Text,
    ToolCall,
    ToolResult,
    json_bytes,
    strict_json,
)


class AnthropicTransport:
    provider = "anthropic"
    ORIGIN = "https://api.anthropic.com"
    MAX_RESPONSE_BYTES = 262_144

    def __init__(self, api_key: str, *, test_transport: httpx.AsyncBaseTransport | None = None):
        if not api_key or any(char.isspace() for char in api_key):
            raise ValueError("invalid_api_key_configuration")
        self._client = httpx.AsyncClient(
            base_url=self.ORIGIN,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            follow_redirects=False,
            trust_env=False,
            transport=test_transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        messages = []
        for message in request.messages:
            content = []
            for part in message.parts:
                if isinstance(part, Text):
                    content.append({"type": "text", "text": part.text})
                elif isinstance(part, ToolCall):
                    content.append(
                        {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
                    )
                elif isinstance(part, ToolResult):
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": part.id,
                            "content": json_bytes(part.data).decode("utf-8"),
                            "is_error": part.is_error,
                        }
                    )
                else:
                    raise Fault("provider_harness", "unsupported_request_part")
            messages.append(
                {"role": "user" if message.role == "tool" else message.role, "content": content}
            )

        payload: dict[str, Any] = {
            "model": request.model,
            "system": request.system,
            "messages": messages,
        }
        if request.tools:
            payload["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
                for tool in request.tools
            ]
            payload["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        if request.output_schema is not None:
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": request.output_schema}
            }
        return payload

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        timeout: float,
        audit: Audit,
    ) -> tuple[dict[str, Any], str | None]:
        if path not in ("/v1/messages", "/v1/messages/count_tokens"):
            raise Fault("provider_harness", "unapproved_provider_endpoint")
        body = json_bytes(payload)
        if len(body) > 262_144:
            raise Fault("provider_harness", "provider_payload_size_limit")

        audit("provider.http.request", provider=self.provider, endpoint=path, payload=payload)
        raw = bytearray()
        request_id = None
        try:
            async with self._client.stream(
                "POST", path, content=body, timeout=httpx.Timeout(timeout)
            ) as response:
                request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
                async for chunk in response.aiter_bytes():
                    room = self.MAX_RESPONSE_BYTES - len(raw)
                    if len(chunk) > room:
                        raw.extend(chunk[:room])
                        audit(
                            "provider.http.response",
                            provider=self.provider,
                            status=response.status_code,
                            request_id=request_id,
                            body=raw.decode("utf-8", errors="replace"),
                            truncated=True,
                        )
                        raise Fault("provider_harness", "provider_response_size_limit")
                    raw.extend(chunk)

                audit(
                    "provider.http.response",
                    provider=self.provider,
                    status=response.status_code,
                    request_id=request_id,
                    body=raw.decode("utf-8", errors="replace"),
                    truncated=False,
                )
                if not 200 <= response.status_code < 300:
                    raise Fault("provider_harness", f"provider_http_{response.status_code}")
        except httpx.TimeoutException:
            raise Fault("provider_harness", "provider_transport_timeout") from None
        except httpx.HTTPError:
            raise Fault("provider_harness", "provider_transport_error") from None

        try:
            value = strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            raise Fault("provider_harness", "invalid_provider_json") from None
        if not isinstance(value, dict):
            raise Fault("provider_harness", "invalid_provider_envelope")
        return value, request_id

    async def count_tokens(self, request: ModelRequest, timeout: float, audit: Audit) -> int:
        value, _ = await self._post(
            "/v1/messages/count_tokens", self._payload(request), timeout, audit
        )
        count = value.get("input_tokens")
        if type(count) is not int or count < 0:
            raise Fault("provider_harness", "invalid_token_count")
        return count

    async def complete(self, request: ModelRequest, timeout: float, audit: Audit) -> ModelResponse:
        payload = self._payload(request)
        payload["max_tokens"] = request.max_output_tokens
        payload["stream"] = False
        value, request_id = await self._post("/v1/messages", payload, timeout, audit)
        try:
            if value["type"] != "message" or value["role"] != "assistant":
                raise ValueError
            content = value["content"]
            usage = value["usage"]
            resolved_model = value["model"]
            response_id = value["id"]
            if not isinstance(content, list) or not isinstance(usage, dict):
                raise ValueError
            if not isinstance(resolved_model, str) or not resolved_model:
                raise ValueError
            if not isinstance(response_id, str) or not response_id:
                raise ValueError

            counts = [
                usage["input_tokens"],
                usage.get("cache_creation_input_tokens", 0),
                usage.get("cache_read_input_tokens", 0),
                usage["output_tokens"],
            ]
            if any(type(count) is not int or count < 0 for count in counts):
                raise ValueError

            parts = []
            unsupported_content = False
            for block in content:
                if not isinstance(block, dict):
                    raise ValueError
                if block.get("type") == "text":
                    if not isinstance(block.get("text"), str):
                        raise ValueError
                    if block.get("citations") not in (None, []):
                        unsupported_content = True
                    parts.append(Text(block["text"]))
                elif block.get("type") == "tool_use":
                    if (
                        not isinstance(block.get("id"), str)
                        or not isinstance(block.get("name"), str)
                        or "input" not in block
                    ):
                        raise ValueError
                    parts.append(ToolCall(block["id"], block["name"], block["input"]))
                else:
                    unsupported_content = True

            finish = {
                "end_turn": "stop",
                "tool_use": "tools",
                "max_tokens": "length",
                "refusal": "refusal",
            }.get(value.get("stop_reason"), "unsupported")
            if unsupported_content:
                finish = "unsupported"
            return ModelResponse(
                message=Message("assistant", tuple(parts)),
                finish=finish,
                input_tokens=sum(counts[:3]),
                output_tokens=counts[3],
                resolved_model=resolved_model,
                response_id=response_id,
                request_id=request_id,
            )
        except (KeyError, TypeError, ValueError):
            raise Fault("provider_harness", "invalid_provider_envelope") from None
