from __future__ import annotations

import asyncio
import re
import weakref
from dataclasses import asdict
from typing import Any
from uuid import uuid4

import httpx

from app.providers.model_contracts import (
    Audit, Fault, Message, ModelRequest, ModelResponse, Text, ToolCall, ToolResult,
    json_bytes, strict_json,
)


class GeminiTransport:
    """Gemini REST adapter; all provider wire state stays within this transport."""

    provider = "gemini"
    ORIGIN = "https://generativelanguage.googleapis.com"
    MAX_RESPONSE_BYTES = 262_144
    MAX_PAYLOAD_BYTES = 262_144
    _MODEL_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"

    def __init__(self, api_key: str, *, test_transport: httpx.AsyncBaseTransport | None = None):
        if not api_key or not api_key.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in api_key):
            raise ValueError("invalid_api_key_configuration")
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.ORIGIN,
            headers={"x-goog-api-key": api_key, "content-type": "application/json"},
            verify=True,
            follow_redirects=False,
            trust_env=False,
            transport=test_transport,
        )
        # Message identity, not equality or response ID, isolates concurrent sessions.
        # Weak references release opaque signatures when the caller drops its history.
        self._continuations: dict[int, tuple[weakref.ReferenceType, str, bytes, bytes]] = {}

    async def aclose(self) -> None:
        self._continuations.clear()
        await self._client.aclose()

    @classmethod
    def _model_name(cls, model: str) -> str:
        model_id = model.removeprefix("models/")
        if re.fullmatch(cls._MODEL_ID, model_id) is None:
            raise Fault("provider_harness", "invalid_provider_model")
        return f"models/{model_id}"

    def _remember(self, message: Message, model: str, content: dict[str, Any]) -> None:
        key = id(message)
        cache = self._continuations
        cache[key] = (
            weakref.ref(message, lambda ref: cache.pop(key, None)),
            model,
            json_bytes(asdict(message)),
            json_bytes(content),
        )

    def _assistant_content(self, message: Message, model: str) -> dict[str, Any]:
        saved = self._continuations.get(id(message))
        if saved is not None and saved[0]() is message:
            if saved[1] != model or saved[2] != json_bytes(asdict(message)):
                raise Fault("provider_harness", "changed_provider_continuation")
            return strict_json(saved[3].decode("utf-8"))
        # Never silently drop a required thought signature from a copied/restored
        # tool turn. M05 retains the actual returned Message objects in memory.
        if any(not isinstance(part, Text) for part in message.parts):
            raise Fault("provider_harness", "missing_provider_continuation")
        return {"role": "model", "parts": [{"text": part.text} for part in message.parts]}

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        model = self._model_name(request.model)
        contents = []
        pending: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for message in request.messages:
            if message.role == "assistant":
                if pending:
                    raise Fault("provider_harness", "missing_tool_result")
                content = self._assistant_content(message, model)
                wire_calls = [part["functionCall"] for part in content["parts"] if "functionCall" in part]
                calls = [part for part in message.parts if isinstance(part, ToolCall)]
                if len(wire_calls) != len(calls):
                    raise Fault("provider_harness", "invalid_provider_continuation")
                for call, wire_call in zip(calls, wire_calls):
                    if call.id in seen:
                        raise Fault("provider_harness", "duplicate_tool_call_id")
                    pending[call.id] = wire_call
                    seen.add(call.id)
                contents.append(content)
                continue
            parts = []
            for part in message.parts:
                if message.role == "user" and isinstance(part, Text) and not pending:
                    parts.append({"text": part.text})
                elif message.role == "tool" and isinstance(part, ToolResult):
                    call = pending.pop(part.id, None)
                    if call is None:
                        raise Fault("provider_harness", "unmatched_tool_result")
                    result = {"name": call["name"], "response": {"error" if part.is_error else "output": part.data}}
                    if "id" in call:
                        result["id"] = call["id"]
                    parts.append({"functionResponse": result})
                else:
                    raise Fault("provider_harness", "unsupported_request_part")
            contents.append({"role": "user", "parts": parts})
        if pending:
            raise Fault("provider_harness", "missing_tool_result")
        payload: dict[str, Any] = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": request.system}]},
            "generationConfig": {"candidateCount": 1, "maxOutputTokens": request.max_output_tokens},
        }
        if request.tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": tool.name, "description": tool.description, "parametersJsonSchema": tool.input_schema}
                for tool in request.tools
            ]}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        if request.output_schema is not None:
            payload["generationConfig"].update(
                responseMimeType="application/json", responseJsonSchema=request.output_schema,
            )
        return payload

    async def _http(
        self, method: str, path: str, payload: dict[str, Any] | None, timeout: float, audit: Audit,
    ) -> tuple[dict[str, Any], str | None]:
        allowed = (
            method == "POST" and re.fullmatch(rf"/v1beta/models/{self._MODEL_ID}:(countTokens|generateContent)", path)
        ) or (method == "GET" and re.fullmatch(rf"/v1beta/models/{self._MODEL_ID}", path) and payload is None)
        if not allowed:
            raise Fault("provider_harness", "unapproved_provider_endpoint")
        body = json_bytes(payload) if payload is not None else b""
        if len(body) > self.MAX_PAYLOAD_BYTES:
            raise Fault("provider_harness", "provider_payload_size_limit")
        # Scrub at the transport boundary even when its caller omitted secrets.
        safe_audit = Audit(lambda event, data: audit(event, **data), secrets=(self._api_key,))
        safe_audit("provider.http.request", provider=self.provider, endpoint=path, payload=payload)
        raw = bytearray()
        request_id = None
        try:
            async with asyncio.timeout(timeout):
                async with self._client.stream(method, path, content=body, timeout=httpx.Timeout(timeout)) as response:
                    request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
                    async for chunk in response.aiter_bytes():
                        room = self.MAX_RESPONSE_BYTES - len(raw)
                        raw.extend(chunk[:room])
                        if len(chunk) > room:
                            safe_audit(
                                "provider.http.response", provider=self.provider, status=response.status_code,
                                request_id=request_id, body=raw.decode("utf-8", errors="replace"), truncated=True,
                            )
                            raise Fault("provider_harness", "provider_response_size_limit")
                    safe_audit(
                        "provider.http.response", provider=self.provider, status=response.status_code,
                        request_id=request_id, body=raw.decode("utf-8", errors="replace"), truncated=False,
                    )
                    if not 200 <= response.status_code < 300:
                        raise Fault("provider_harness", f"provider_http_{response.status_code}")
        except (httpx.TimeoutException, TimeoutError):
            raise Fault("provider_harness", "provider_transport_timeout") from None
        except httpx.HTTPError:
            raise Fault("provider_harness", "provider_transport_error") from None
        try:
            value = strict_json(raw.decode("utf-8"), max_bytes=self.MAX_RESPONSE_BYTES)
        except (ValueError, UnicodeError, RecursionError):
            raise Fault("provider_harness", "invalid_provider_json") from None
        if not isinstance(value, dict):
            raise Fault("provider_harness", "invalid_provider_envelope")
        if "error" in value:
            raise Fault("provider_harness", "provider_error_envelope")
        return value, request_id

    async def verify_model(self, model: str, timeout: float, audit: Audit) -> None:
        """Opt-in preflight only; no generation, fallback, retry, or core RPC changes."""
        name = self._model_name(model)
        value, _ = await self._http("GET", f"/v1beta/{name}", None, timeout, audit)
        methods = value.get("supportedGenerationMethods")
        if value.get("name") != name or not isinstance(methods, list) or "generateContent" not in methods:
            raise Fault("provider_harness", "provider_model_unavailable")
        audit("provider.model.checked", provider=self.provider, requested_model=model, available=True)

    async def count_tokens(self, request: ModelRequest, timeout: float, audit: Audit) -> int:
        name = self._model_name(request.model)
        payload = {"generateContentRequest": {"model": name, **self._payload(request)}}
        value, _ = await self._http("POST", f"/v1beta/{name}:countTokens", payload, timeout, audit)
        count = value.get("totalTokens")
        if type(count) is not int or count < 0:
            raise Fault("provider_harness", "invalid_token_count")
        return count

    async def complete(self, request: ModelRequest, timeout: float, audit: Audit) -> ModelResponse:
        name = self._model_name(request.model)
        value, request_id = await self._http("POST", f"/v1beta/{name}:generateContent", self._payload(request), timeout, audit)
        try:
            if value.get("promptFeedback", {}).get("blockReason"):
                # Do not invent a completion or zero usage for a blocked prompt.
                raise Fault("provider_harness", "provider_prompt_blocked")
            candidates = value["candidates"]
            if not isinstance(candidates, list) or len(candidates) != 1:
                raise ValueError
            candidate = candidates[0]
            content = candidate.get("content", {"role": "model", "parts": []})
            if content.get("role") != "model" or not isinstance(content.get("parts"), list):
                raise ValueError
            resolved_model, response_id = value["modelVersion"], value["responseId"]
            if not all(isinstance(item, str) and item for item in (resolved_model, response_id)):
                raise ValueError
            usage = value["usageMetadata"]
            input_tokens = usage["promptTokenCount"]
            total_tokens = usage["totalTokenCount"]
            candidates_tokens = usage.get("candidatesTokenCount", 0)
            thought_tokens = usage.get("thoughtsTokenCount", 0)
            cached_tokens = usage.get("cachedContentTokenCount", 0)
            tool_tokens = usage.get("toolUsePromptTokenCount", 0)
            counts = (input_tokens, total_tokens, candidates_tokens, thought_tokens, cached_tokens, tool_tokens)
            if any(type(count) is not int or count < 0 for count in counts):
                raise ValueError
            # Cached prompt tokens are already included. Thinking counts against
            # the existing output and total budgets, even without visible text.
            output_tokens = total_tokens - input_tokens
            if output_tokens != candidates_tokens + thought_tokens or cached_tokens > input_tokens or tool_tokens:
                raise ValueError
            parts = []
            unsupported = False
            local_prefix = f"gemini_{uuid4().hex}"
            for index, block in enumerate(content["parts"]):
                if not isinstance(block, dict):
                    raise ValueError
                if "thought" in block and type(block["thought"]) is not bool:
                    raise ValueError
                if "thoughtSignature" in block and not isinstance(block["thoughtSignature"], str):
                    raise ValueError
                if "text" in block and "functionCall" not in block:
                    if not isinstance(block["text"], str):
                        raise ValueError
                    if not block.get("thought", False):
                        parts.append(Text(block["text"]))
                    unsupported |= bool(set(block) - {"text", "thought", "thoughtSignature"})
                elif "functionCall" in block and "text" not in block and not block.get("thought", False):
                    call = block["functionCall"]
                    if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not call["name"]:
                        raise ValueError
                    call_id = call.get("id", f"{local_prefix}_{index}")
                    if not isinstance(call_id, str) or not call_id:
                        raise ValueError
                    parts.append(ToolCall(call_id, call["name"], call.get("args", {})))
                    unsupported |= bool(set(call) - {"id", "name", "args"})
                    unsupported |= bool(set(block) - {"functionCall", "thoughtSignature", "thought"})
                elif block.get("thoughtSignature") and not (set(block) - {"thought", "thoughtSignature"}):
                    pass  # Opaque signature-only continuation part.
                else:
                    unsupported = True
            reason = candidate.get("finishReason")
            finish = {
                "STOP": "tools" if any(isinstance(part, ToolCall) for part in parts) else "stop",
                "MAX_TOKENS": "length",
                "SAFETY": "refusal", "RECITATION": "refusal", "BLOCKLIST": "refusal",
                "PROHIBITED_CONTENT": "refusal", "SPII": "refusal",
            }.get(reason, "unsupported")
            if unsupported or (finish in ("stop", "tools") and not parts):
                finish = "unsupported"
            message = Message("assistant", tuple(parts))
            self._remember(message, name, content)
            return ModelResponse(message, finish, input_tokens, output_tokens, resolved_model, response_id, request_id)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise Fault("provider_harness", "invalid_provider_envelope") from None
