import asyncio
import json

import httpx
import pytest

from app.providers.anthropic_transport import AnthropicTransport
from app.providers.model_contracts import Audit, Fault, Message, ModelRequest, Text, ToolSpec


def request(output_schema=None):
    return ModelRequest(
        model="fixture-model",
        system="system",
        messages=(Message("user", (Text("hello"),)),),
        tools=(ToolSpec("list_files", "list", {"type":"object","properties":{},"required":[],"additionalProperties":False}),),
        max_output_tokens=50,
        output_schema=output_schema,
    )


def test_mock_http_transport_uses_only_approved_paths_and_does_not_log_key():
    seen = []
    async def handler(req):
        seen.append((str(req.url), req.headers.get("x-api-key"), json.loads(req.content)))
        if req.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 7}, headers={"request-id":"count-id"})
        return httpx.Response(200, json={
            "type":"message", "role":"assistant", "content":[{"type":"text","text":"done"}],
            "usage":{"input_tokens":7,"output_tokens":1}, "model":"resolved", "id":"msg-1", "stop_reason":"end_turn"
        }, headers={"request-id":"msg-id"})

    events = []
    transport = AnthropicTransport("secret-key", test_transport=httpx.MockTransport(handler))
    try:
        count = asyncio.run(transport.count_tokens(request(), 5, Audit(lambda k,p: events.append((k,p)), secrets=("secret-key",))))
        response = asyncio.run(transport.complete(request(), 5, Audit(lambda k,p: events.append((k,p)), secrets=("secret-key",))))
    finally:
        asyncio.run(transport.aclose())
    assert count == 7
    assert response.resolved_model == "resolved"
    assert {url.split("api.anthropic.com")[-1] for url, _, _ in seen} == {"/v1/messages/count_tokens", "/v1/messages"}
    assert all(key == "secret-key" for _, key, _ in seen)
    assert "secret-key" not in repr(events)


def test_provider_error_is_provider_harness_failure():
    async def handler(req):
        return httpx.Response(401, json={"error":{"message":"bad key"}})
    transport = AnthropicTransport("secret-key", test_transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Fault) as err:
            asyncio.run(transport.count_tokens(request(), 5, Audit(lambda k,p: None)))
    finally:
        asyncio.run(transport.aclose())
    assert err.value.kind == "provider_harness"
    assert err.value.code == "provider_http_401"


def test_malformed_provider_json_fails_closed():
    async def handler(req):
        return httpx.Response(200, content=b"not-json")
    transport = AnthropicTransport("secret-key", test_transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Fault) as err:
            asyncio.run(transport.count_tokens(request(), 5, Audit(lambda k,p: None)))
    finally:
        asyncio.run(transport.aclose())
    assert err.value.code == "invalid_provider_json"
