import json
import runpy
from pathlib import Path

import httpx
import pytest

from app.providers.gemini_transport import GeminiTransport
from app.providers.model_contracts import Fault

from .test_gemini_transport import call, envelope
from .test_model_config import clear


@pytest.fixture
def wiring(monkeypatch):
    clear(monkeypatch)
    module = runpy.run_path(str(Path(__file__).parents[1] / "live/test_mission05_live.py"))
    # Functions share this globals dictionary, distinct from runpy's result copy.
    namespace = module["require_live_role"].__globals__
    return namespace


def configure(monkeypatch):
    monkeypatch.setenv("AEGIS_RUN_LIVE_TESTS", "1")
    for prefix in ("AEGIS_AUT", "AEGIS_DIAG"):
        monkeypatch.setenv(f"{prefix}_PROVIDER", "gemini")
        monkeypatch.setenv(f"{prefix}_MODEL", "fixture-model")
        monkeypatch.setenv(f"{prefix}_API_KEY", "fixture-secret")


def test_live_disabled_and_missing_role_never_build_transport(wiring, monkeypatch, tmp_path):
    def unexpected(config):
        pytest.fail("disabled live test attempted transport creation")
    monkeypatch.setitem(wiring, "build_transport", unexpected)
    for name in ("test_live_model_agent_sales_report", "test_live_model_diagnosis_remains_grounded", "test_live_combined_agent_and_diagnosis"):
        with pytest.raises(pytest.skip.Exception):
            wiring[name](tmp_path)
    monkeypatch.setenv("AEGIS_RUN_LIVE_TESTS", "1")
    with pytest.raises(pytest.fail.Exception, match="live provider configuration is incomplete"):
        wiring["test_live_model_agent_sales_report"](tmp_path)


@pytest.mark.parametrize("name", ["test_live_model_agent_sales_report", "test_live_model_diagnosis_remains_grounded", "test_live_combined_agent_and_diagnosis"])
def test_live_checks_work_with_mocked_http_and_export_evidence(wiring, monkeypatch, tmp_path, name):
    configure(monkeypatch)
    monkeypatch.setenv("AEGIS_RUN_COMBINED_LIVE_TEST", "1")
    requests, transports = [], []
    def handler(req):
        requests.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"name": "models/fixture-model", "supportedGenerationMethods": ["generateContent"]})
        if req.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 7})
        payload = json.loads(req.content)
        if "tools" in payload:
            return httpx.Response(200, json=envelope([call("write_report", {"content": '{"order_count":3,"total_revenue":600.0}'})]))
        return httpx.Response(200, json=envelope([{"text": '{"hypotheses":[]}'}]))
    def build(config):
        transport = GeminiTransport(config.api_key, test_transport=httpx.MockTransport(handler))
        transports.append(transport)
        return transport
    monkeypatch.setitem(wiring, "build_transport", build)
    wiring[name](tmp_path)
    assert requests[0].method == "GET"
    assert all(t._client.is_closed for t in transports)
    evidence = list(tmp_path.glob("*.json"))
    assert evidence
    assert "fixture-secret" not in "".join(p.read_text() for p in evidence)


def test_unavailable_live_model_does_not_generate(wiring, monkeypatch, tmp_path):
    configure(monkeypatch)
    methods = []
    def handler(req):
        methods.append(req.method)
        return httpx.Response(404, json={"error": {"message": "not available"}})
    monkeypatch.setitem(wiring, "build_transport", lambda config: GeminiTransport(config.api_key, test_transport=httpx.MockTransport(handler)))
    with pytest.raises(Fault, match="provider_http_404"):
        wiring["test_live_model_agent_sales_report"](tmp_path)
    assert methods == ["GET"]


@pytest.mark.parametrize("failure_stage", ["countTokens", "generateContent"])
@pytest.mark.parametrize("name", ["test_live_model_diagnosis_remains_grounded", "test_live_combined_agent_and_diagnosis"])
def test_live_diagnosis_provider_failure_cannot_pass(wiring, monkeypatch, tmp_path, name, failure_stage):
    configure(monkeypatch)
    monkeypatch.setenv("AEGIS_RUN_COMBINED_LIVE_TEST", "1")
    monkeypatch.setenv("AEGIS_AUT_MODEL", "agent-fixture")
    monkeypatch.setenv("AEGIS_DIAG_MODEL", "diagnostic-fixture")
    requests, transports = [], []
    def handler(req):
        requests.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json={"name": req.url.path.removeprefix("/v1beta/"), "supportedGenerationMethods": ["generateContent"]})
        if req.url.path == f"/v1beta/models/diagnostic-fixture:{failure_stage}":
            return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand fixture-secret"}})
        if req.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 7})
        if req.url.path.startswith("/v1beta/models/agent-fixture:"):
            return httpx.Response(200, json=envelope([call("write_report", {"content": '{"order_count":3,"total_revenue":600.0}'})]))
        pytest.fail("unexpected generation after diagnostic failure")
    def build(config):
        transport = GeminiTransport(config.api_key, test_transport=httpx.MockTransport(handler))
        transports.append(transport)
        return transport
    monkeypatch.setitem(wiring, "build_transport", build)
    with pytest.raises(AssertionError, match="provider_http_503"):
        wiring[name](tmp_path)
    diagnostic_requests = [item for item in requests if "diagnostic-fixture" in item[1]]
    expected = [("GET", "/v1beta/models/diagnostic-fixture"), ("POST", "/v1beta/models/diagnostic-fixture:countTokens")]
    if failure_stage == "generateContent":
        expected.append(("POST", "/v1beta/models/diagnostic-fixture:generateContent"))
    assert diagnostic_requests == expected  # No retry or fallback at either stage.
    assert all(t._client.is_closed for t in transports)
    evidence = [json.loads(p.read_text()) for p in tmp_path.glob("*.json")]
    saved = next(item for item in evidence if "diagnosis" in item)
    metadata = saved["run"]["model_execution"]["diagnostic"]
    assert metadata["failure_kind"] == "provider_harness"
    assert metadata["failure_code"] == "provider_http_503"
    assert metadata["validation_status"] == "provider_failure"
    assert metadata["accounting"]["usage_unknown"] is (failure_stage == "generateContent")
    assert saved["diagnosis"]["status"] == "inconclusive"
    assert saved["diagnosis"]["hypotheses"] == []
    if name == "test_live_combined_agent_and_diagnosis":
        agent_only = next(item["run"] for item in evidence if "agent" in item.get("run", {}).get("model_execution", {}) and "diagnosis" not in item)
        assert saved["run"]["status"] == agent_only["status"] == "passed"
        assert saved["run"]["validations"] == agent_only["validations"]
        assert saved["run"]["model_execution"]["agent"] == agent_only["model_execution"]["agent"]
    assert "fixture-secret" not in json.dumps(evidence)


def test_combined_requires_separate_opt_in(wiring, monkeypatch, tmp_path):
    configure(monkeypatch)
    def unexpected(config):
        pytest.fail("combined test attempted transport creation without opt-in")
    monkeypatch.setitem(wiring, "build_transport", unexpected)
    with pytest.raises(pytest.skip.Exception):
        wiring["test_live_combined_agent_and_diagnosis"](tmp_path)


@pytest.mark.parametrize("prefix,name", [
    ("AEGIS_AUT", "test_live_model_agent_sales_report"),
    ("AEGIS_DIAG", "test_live_model_diagnosis_remains_grounded"),
])
@pytest.mark.parametrize("field", ["PROVIDER", "MODEL", "API_KEY"])
def test_explicit_live_missing_configuration_fails_before_network(wiring, monkeypatch, tmp_path, prefix, name, field):
    configure(monkeypatch)
    monkeypatch.delenv(f"{prefix}_{field}")
    def unexpected(config):
        pytest.fail("incomplete live configuration attempted transport creation")
    monkeypatch.setitem(wiring, "build_transport", unexpected)
    with pytest.raises(pytest.fail.Exception, match="live provider configuration is incomplete"):
        wiring[name](tmp_path)
    assert not list(tmp_path.iterdir())
