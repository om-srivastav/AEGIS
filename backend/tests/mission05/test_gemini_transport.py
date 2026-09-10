import asyncio
import gc
import json
from dataclasses import replace

import httpx
import pytest

from app.agents.model_tools import TOOL_SPECS
from app.providers.gemini_transport import GeminiTransport
from app.providers.model_contracts import Audit, Fault, Message, ModelRequest, Text, ToolCall, ToolResult
from app.providers.model_reasoning import diagnosis_schema


def request(**changes):
    return replace(ModelRequest("fixture-model", "system", (Message("user", (Text("hello"),)),), TOOL_SPECS, 50), **changes)


def envelope(parts=None, reason="STOP", **changes):
    return {
        "candidates": [{"content": {"role": "model", "parts": parts if parts is not None else [{"text": "done"}]}, "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2, "thoughtsTokenCount": 3, "totalTokenCount": 12},
        "modelVersion": "fixture-resolved", "responseId": "response-1", **changes,
    }


def call(name="list_files", args=None, **fields):
    return {"functionCall": {"name": name, "args": {} if args is None else args, **fields}, "thoughtSignature": "opaque-signature"}


def execute(handler, operation):
    async def run():
        transport = GeminiTransport("fixture-secret", test_transport=httpx.MockTransport(handler))
        try:
            return await operation(transport)
        finally:
            await transport.aclose()
    return asyncio.run(run())


def test_fixed_origin_full_token_payload_schema_and_metadata(caplog):
    seen, events = [], []
    def handler(req):
        seen.append(req)
        if req.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 7})
        return httpx.Response(200, json=envelope(), headers={"x-request-id": "request-1"})
    async def check(t):
        req = request(tools=(), output_schema=diagnosis_schema())
        audit = Audit(lambda k, p: events.append((k, p)))
        assert await t.count_tokens(req, 5, audit) == 7
        result = await t.complete(req, 5, audit)
        assert (result.finish, result.input_tokens, result.output_tokens) == ("stop", 7, 5)
        assert (result.resolved_model, result.response_id, result.request_id) == ("fixture-resolved", "response-1", "request-1")
    execute(handler, check)
    assert [r.url.path for r in seen] == ["/v1beta/models/fixture-model:countTokens", "/v1beta/models/fixture-model:generateContent"]
    assert all(r.url.scheme == "https" and r.url.host == "generativelanguage.googleapis.com" and not r.url.query for r in seen)
    assert all(r.headers["x-goog-api-key"] == "fixture-secret" for r in seen)
    counted = json.loads(seen[0].content)["generateContentRequest"]
    generated = json.loads(seen[1].content)
    assert counted.pop("model") == "models/fixture-model"
    assert counted == generated
    assert generated["generationConfig"]["responseJsonSchema"] == diagnosis_schema()
    assert generated["generationConfig"]["responseMimeType"] == "application/json"
    assert generated["generationConfig"]["maxOutputTokens"] == 50
    assert generated["systemInstruction"] == {"parts": [{"text": "system"}]}
    assert "tools" not in generated
    assert "fixture-secret" not in repr(events) + caplog.text


@pytest.mark.parametrize("provider_id", [None, "provider-call-1"])
@pytest.mark.parametrize("is_error", [False, True])
def test_tool_continuation_preserves_parts_and_correlates_results(provider_id, is_error):
    seen = []
    wire = [
        {"text": "private thought", "thought": True, "thoughtSignature": "thought-sig"},
        {"text": "checking", "thoughtSignature": "text-sig"},
        {"thoughtSignature": "signature-only"},
        call("read_file", {"path": "sales.csv"}, **({"id": provider_id} if provider_id else {})),
    ]
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"totalTokens": 8} if req.url.path.endswith(":countTokens") else envelope(wire))
    async def check(t):
        first = await t.complete(request(), 5, Audit(lambda k, p: None))
        assert first.finish == "tools"
        assert "private thought" not in repr(first)
        tool = first.message.parts[-1]
        assert isinstance(tool, ToolCall)
        assert (tool.id == provider_id) if provider_id else tool.id.startswith("gemini_")
        data = {"error": "not_found"} if is_error else {"content": "csv"}
        following = request(messages=(*request().messages, first.message, Message("tool", (ToolResult(tool.id, data, is_error),))))
        await t.count_tokens(following, 5, Audit(lambda k, p: None))
        await t.complete(following, 5, Audit(lambda k, p: None))
        for body in (seen[1]["generateContentRequest"], seen[2]):
            assert body["contents"][1] == {"role": "model", "parts": wire}
            expected = {"name": "read_file", "response": {"error" if is_error else "output": data}}
            if provider_id:
                expected["id"] = provider_id
            assert body["contents"][2] == {"role": "user", "parts": [{"functionResponse": expected}]}
        declarations = seen[0]["tools"][0]["functionDeclarations"]
        assert [d["name"] for d in declarations] == [s.name for s in TOOL_SPECS]
        assert [d["parametersJsonSchema"] for d in declarations] == [s.input_schema for s in TOOL_SPECS]
    execute(handler, check)


def test_continuations_isolate_sessions_and_release_history():
    counter = 0
    def handler(req):
        nonlocal counter
        counter += 1
        return httpx.Response(200, json=envelope([call("read_file", {"path": str(counter)})]))
    async def check(t):
        audit = Audit(lambda k, p: None)
        first, second = await asyncio.gather(t.complete(request(), 5, audit), t.complete(request(), 5, audit))
        assert first.message.parts[0].id != second.message.parts[0].id
        for response, path in ((second, "2"), (first, "1")):
            tool = response.message.parts[0]
            req = request(messages=(response.message, Message("tool", (ToolResult(tool.id, {}),))))
            assert t._payload(req)["contents"][0]["parts"][0]["functionCall"]["args"]["path"] == path
        del first, second, response, req
        await asyncio.sleep(0)
        gc.collect()
        assert not t._continuations
    execute(handler, check)


@pytest.mark.parametrize("change,code", [("copy", "missing_provider_continuation"), ("model", "changed_provider_continuation"), ("arguments", "changed_provider_continuation"), ("result", "unmatched_tool_result")])
def test_invalid_continuation_fails_before_http(change, code):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=envelope([call()]))
    async def check(t):
        response = await t.complete(request(), 5, Audit(lambda k, p: None))
        message = response.message
        tool = message.parts[0]
        if change == "copy":
            message = replace(message)
        if change == "arguments":
            tool.arguments["extra"] = 1
        following = request(model="other-model" if change == "model" else "fixture-model", messages=(message, Message("tool", (ToolResult("wrong" if change == "result" else tool.id, {}),))))
        with pytest.raises(Fault, match=code):
            await t.count_tokens(following, 5, Audit(lambda k, p: None))
    execute(handler, check)
    assert len(seen) == 1


@pytest.mark.parametrize("status", [301, 307, 400, 401, 403, 404, 429, 500, 503])
def test_http_errors_no_retry_redirect_or_secret_leak(status, caplog):
    seen, events = [], []
    def handler(req):
        seen.append(req)
        return httpx.Response(status, json={"error": {"message": "fixture-secret"}}, headers={"location": "https://example.com/collect", "x-request-id": "fixture-secret"})
    async def check(t):
        with pytest.raises(Fault) as exc:
            await t.complete(request(), 5, Audit(lambda k, p: events.append((k, p))))
        assert exc.value.kind == "provider_harness"
        assert exc.value.code == f"provider_http_{status}"
        assert "fixture-secret" not in str(exc.value)
    execute(handler, check)
    assert len(seen) == 1
    assert "fixture-secret" not in repr(events) + caplog.text


@pytest.mark.parametrize("body,code", [
    (b'{"totalTokens":1,"totalTokens":2}', "invalid_provider_json"),
    (b'{"totalTokens":NaN}', "invalid_provider_json"),
    (b'\xff', "invalid_provider_json"), (b'[]', "invalid_provider_envelope"),
    (b'{"error":{}}', "provider_error_envelope"),
    (b'{"totalTokens":true}', "invalid_token_count"),
    (b'{"totalTokens":-1}', "invalid_token_count"), (b'{}', "invalid_token_count"),
])
def test_bad_token_responses_fail_closed(body, code):
    async def check(t):
        with pytest.raises(Fault, match=code) as exc:
            await t.count_tokens(request(), 5, Audit(lambda k, p: None))
        assert exc.value.kind == "provider_harness"
    execute(lambda req: httpx.Response(200, content=body), check)


@pytest.mark.parametrize("changes", [
    {"candidates": []}, {"candidates": [{}, {}]}, {"candidates": [None]},
    {"modelVersion": ""}, {"responseId": None}, {"promptFeedback": None},
    {"usageMetadata": {}},
    {"usageMetadata": {"promptTokenCount": True, "totalTokenCount": 5}},
    {"usageMetadata": {"promptTokenCount": 7, "totalTokenCount": 5}},
    {"usageMetadata": {"promptTokenCount": 7, "totalTokenCount": 12}},
])
def test_invalid_completion_envelopes_are_provider_failures(changes):
    async def check(t):
        with pytest.raises(Fault, match="invalid_provider_envelope") as exc:
            await t.complete(request(), 5, Audit(lambda k, p: None))
        assert exc.value.kind == "provider_harness"
    execute(lambda req: httpx.Response(200, json=envelope(**changes)), check)


@pytest.mark.parametrize("reason,parts,finish", [
    ("STOP", [call()], "tools"), ("STOP", [{"text": "ok"}], "stop"),
    ("MAX_TOKENS", [call()], "length"), ("SAFETY", [], "refusal"),
    ("RECITATION", [], "refusal"), ("MALFORMED_FUNCTION_CALL", [], "unsupported"),
    ("NEW_REASON", [call()], "unsupported"),
    ("STOP", [call(), {"executableCode": {"code": "no"}}], "unsupported"),
    ("STOP", [{"text": "only thinking", "thought": True}], "unsupported"),
])
def test_finish_normalization(reason, parts, finish):
    async def check(t):
        assert (await t.complete(request(), 5, Audit(lambda k, p: None))).finish == finish
    execute(lambda req: httpx.Response(200, json=envelope(parts, reason)), check)


def test_blocked_prompt_without_usage_is_explicit_provider_failure():
    async def check(t):
        with pytest.raises(Fault, match="provider_prompt_blocked"):
            await t.complete(request(), 5, Audit(lambda k, p: None))
    execute(lambda req: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), check)


def test_cached_tokens_not_double_counted_and_zero_completion_supported():
    async def check(t):
        response = await t.complete(request(), 5, Audit(lambda k, p: None))
        assert (response.input_tokens, response.output_tokens, response.finish) == (7, 0, "refusal")
    execute(lambda req: httpx.Response(200, json=envelope([], "SAFETY", usageMetadata={"promptTokenCount": 7, "cachedContentTokenCount": 4, "totalTokenCount": 7})), check)


@pytest.mark.parametrize("model", ["../escape", "models/../escape", "https://example.com", "ok?key=x", "ok#fragment", "ok%2fescape", "models/ok/more", "a" * 129])
def test_model_cannot_change_endpoint(model):
    async def check(t):
        with pytest.raises(Fault, match="invalid_provider_model"):
            await t.complete(request(model=model), 5, Audit(lambda k, p: None))
    execute(lambda req: pytest.fail("must not contact network"), check)


def test_unapproved_http_method_or_path_rejected():
    async def check(t):
        for method, path in [("GET", "/v1beta/models/ok:generateContent"), ("POST", "/v1beta/files"), ("POST", "https://example.com")]:
            with pytest.raises(Fault, match="unapproved_provider_endpoint"):
                await t._http(method, path, {}, 5, Audit(lambda k, p: None))
    execute(lambda req: pytest.fail("must not contact network"), check)


@pytest.mark.parametrize("limit", ["request", "response"])
def test_wire_size_limits(limit):
    async def check(t):
        t.MAX_PAYLOAD_BYTES = 10 if limit == "request" else 262_144
        t.MAX_RESPONSE_BYTES = 10
        with pytest.raises(Fault, match="provider_payload_size_limit" if limit == "request" else "provider_response_size_limit"):
            await t.complete(request(), 5, Audit(lambda k, p: None))
    execute(lambda req: httpx.Response(200, content=b"x" * 100), check)


@pytest.mark.parametrize("failure,code", [(httpx.ReadTimeout, "provider_transport_timeout"), (httpx.ConnectError, "provider_transport_error")])
def test_network_exception_normalization(failure, code):
    def handler(req):
        raise failure("fixture-secret", request=req)
    async def check(t):
        with pytest.raises(Fault, match=code):
            await t.complete(request(), 5, Audit(lambda k, p: None))
    execute(handler, check)


def test_whole_response_deadline_and_cancellation():
    async def handler(req):
        await asyncio.Event().wait()
    async def check(t):
        with pytest.raises(Fault, match="provider_transport_timeout"):
            await t.complete(request(), 0.01, Audit(lambda k, p: None))
        task = asyncio.create_task(t.complete(request(), 5, Audit(lambda k, p: None)))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    execute(handler, check)


def test_production_client_security_options(monkeypatch):
    real_client, options = httpx.AsyncClient, {}
    def client(**kwargs):
        options.update(kwargs)
        return real_client(**kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    execute(lambda req: httpx.Response(200), lambda t: asyncio.sleep(0))
    assert options["verify"] is True
    assert options["follow_redirects"] is False
    assert options["trust_env"] is False
    assert options["base_url"] == GeminiTransport.ORIGIN


@pytest.mark.parametrize("available", [True, False])
def test_key_scoped_model_check_no_generation_or_fallback(available):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"name": "models/fixture-model", "supportedGenerationMethods": ["generateContent"] if available else ["embedContent"]})
    async def check(t):
        if available:
            await t.verify_model("models/fixture-model", 5, Audit(lambda k, p: None))
        else:
            with pytest.raises(Fault, match="provider_model_unavailable"):
                await t.verify_model("fixture-model", 5, Audit(lambda k, p: None))
    execute(handler, check)
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v1beta/models/fixture-model"
    assert not seen[0].content
