import asyncio
import json

import httpx
import pytest

from app.models.schemas import DiagnosisStatus, EventType, PairTransition, PerturbationKind, RunStatus
from app.providers.model_config import build_transport, read_settings
from app.providers.model_contracts import Limits
from app.providers.model_reasoning import ModelReasoningProvider, project_diagnosis_request
from app.providers.reasoning import MockProvider
from app.services.diagnosis import DiagnosisService
from app.services.regression import RegressionVaultService
from app.services.repair import RepairService
from app.services.runner import RunService

from .test_gemini_transport import call, envelope, execute
from .test_model_config import clear
from .test_model_reasoning import _request


@pytest.mark.parametrize("agent_provider,diag_provider", [("gemini", "anthropic"), ("anthropic", "gemini"), ("gemini", "gemini")])
def test_gemini_config_keeps_roles_independent(monkeypatch, agent_provider, diag_provider):
    clear(monkeypatch)
    monkeypatch.setenv("AEGIS_AGENT_ADAPTER", "model")
    monkeypatch.setenv("AEGIS_REASONING_PROVIDER", "model")
    for prefix, provider in (("AEGIS_AUT", agent_provider), ("AEGIS_DIAG", diag_provider)):
        monkeypatch.setenv(f"{prefix}_PROVIDER", provider)
        monkeypatch.setenv(f"{prefix}_MODEL", f"{prefix}-model")
        monkeypatch.setenv(f"{prefix}_API_KEY", f"{prefix}-secret")
    monkeypatch.setenv("AEGIS_DIAG_MAX_OUTPUT_TOKENS", "4000")
    settings = read_settings()
    assert settings.agent.provider == agent_provider
    assert settings.diagnostic.provider == diag_provider
    assert settings.agent.limits.max_output_tokens == 2048
    assert settings.diagnostic.limits.max_output_tokens == 4000
    async def check():
        for config in (settings.agent, settings.diagnostic):
            transport = build_transport(config)
            try:
                assert transport.provider == config.provider
                assert config.api_key not in repr(config) + repr(config.public_metadata())
            finally:
                await transport.aclose()
    asyncio.run(check())
    monkeypatch.delenv("AEGIS_DIAG_API_KEY")
    with pytest.raises(ValueError, match="incomplete_model_configuration:AEGIS_DIAG"):
        read_settings()


def test_unknown_provider_still_rejected(monkeypatch):
    clear(monkeypatch)
    for name, value in {"AEGIS_AGENT_ADAPTER": "model", "AEGIS_AUT_PROVIDER": "other", "AEGIS_AUT_MODEL": "x", "AEGIS_AUT_API_KEY": "secret"}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="unsupported_provider"):
        read_settings()


@pytest.mark.parametrize("correct", [True, False])
def test_gemini_http_agent_recovery_and_deterministic_report_authority(correct):
    responses = [
        call("read_file", {"path": "sales.csv"}), call(),
        call("read_file", {"path": "archive/sales.csv"}),
        call("write_report", {"content": '{"order_count":3,"total_revenue":600.0}' if correct else '{"order_count":99,"total_revenue":1}'}),
    ]
    seen = []
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"totalTokens": 7} if req.url.path.endswith(":countTokens") else envelope([responses.pop(0)]))
    async def check(t):
        run = await RunService().execute_model(PerturbationKind.FILE_MOVED, transport=t, model="fixture-model", limits=Limits(), secrets=("fixture-secret",))
        assert run.status == (RunStatus.PASSED if correct else RunStatus.FAILED)
        metadata = run.model_execution["agent"]
        assert metadata["execution_status"] == "completed"
        assert metadata["provider"] == "gemini"
        assert metadata["resolved_models"] == ["fixture-resolved"]
        assert metadata["accounting"]["provider_requests"] == 8
        assert metadata["accounting"]["output_tokens"] == 20
        assert next(v for v in run.validations if v.name == "report_values").passed is correct
        assert any(e.kind == EventType.TOOL_RESULT and e.payload.get("error_code") == "not_found" for e in run.events)
        assert run.trace_complete and run.trace_hash
        assert "fixture-secret" not in run.model_dump_json()
    execute(handler, check)
    assert len(seen) == 8


@pytest.mark.parametrize("blocks,code", [
    ([call("shell", {"command": "whoami"})], "unknown_tool"),
    ([call("read_file", {"path": "../outside"})], "invalid_virtual_path"),
    ([call("read_file", {"path": 1})], "malformed_tool_arguments"),
    ([call(), call()], "parallel_tool_calls_rejected"),
    ([call(), {"executableCode": {"code": "no"}}], "unsupported_provider_response"),
])
def test_gemini_rejected_calls_never_dispatch(blocks, code):
    def handler(req):
        return httpx.Response(200, json={"totalTokens": 7} if req.url.path.endswith(":countTokens") else envelope(blocks))
    async def check(t):
        run = await RunService().execute_model(PerturbationKind.NONE, transport=t, model="fixture-model")
        assert run.model_execution["agent"]["failure_code"] == code
        assert not any(e.kind == EventType.TOOL_CALL for e in run.events)
    execute(handler, check)


def test_gemini_repeated_provider_call_id_is_rejected():
    def handler(req):
        return httpx.Response(200, json={"totalTokens": 7} if req.url.path.endswith(":countTokens") else envelope([call(id="repeated")]))
    async def check(t):
        run = await RunService().execute_model(PerturbationKind.NONE, transport=t, model="fixture-model")
        assert run.model_execution["agent"]["failure_code"] == "duplicate_tool_call_id"
        assert sum(e.kind == EventType.TOOL_CALL for e in run.events) == 1
    execute(handler, check)


def test_thinking_tokens_enforce_existing_total_budget_before_dispatch():
    def handler(req):
        body = {"totalTokens": 7} if req.url.path.endswith(":countTokens") else envelope([call()], usageMetadata={"promptTokenCount": 7, "candidatesTokenCount": 2, "thoughtsTokenCount": 600, "totalTokenCount": 609})
        return httpx.Response(200, json=body)
    async def check(t):
        run = await RunService().execute_model(PerturbationKind.NONE, transport=t, model="fixture-model", limits=Limits(max_total_tokens=550))
        assert run.model_execution["agent"]["failure_code"] == "total_token_limit"
        assert not any(e.kind == EventType.TOOL_CALL for e in run.events)
    execute(handler, check)


def test_mocked_gemini_http_failure_is_invalid_regression_evidence():
    service = RunService()
    def handler(req):
        return httpx.Response(200, json={"totalTokens": 7}) if req.url.path.endswith(":countTokens") else httpx.Response(503, json={"error": {"message": "unavailable"}})
    async def check(t):
        candidate = await service.execute_model(PerturbationKind.NONE, transport=t, model="fixture-model")
        assert candidate.model_execution["agent"]["failure_kind"] == "provider_harness"
        assert candidate.model_execution["agent"]["accounting"]["usage_unknown"] is True
        baseline = service.execute(PerturbationKind.NONE)
        repair = RepairService(service, DiagnosisService(service))
        vault = RegressionVaultService(service, repair)
        case = vault.register_case(baseline.run_id, name="clean")
        result = vault._pair_result(type("Member", (), {"role": "historical"})(), case, baseline, candidate, repair.compare(baseline, candidate))
        assert result.valid_pair is False
        assert result.transition == PairTransition.INVALID
        assert any("provider/harness" in reason for reason in result.reasons)
    execute(handler, check)


@pytest.mark.parametrize("result_kind", ["valid", "ungrounded", "malformed", "outage"])
def test_gemini_diagnosis_keeps_projection_and_validator_authority(result_kind):
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    run.scenario.fixture_snapshot["private"] = "HIDDEN_TRUTH_NOT_SENT"
    req = _request(run)
    candidate = asyncio.run(MockProvider().diagnose(req))
    if result_kind == "ungrounded":
        candidate.hypotheses[0].supporting_evidence[0].event_id = "missing-event"
    seen, expected = [], []
    def handler(http_request):
        body = json.loads(http_request.content)
        seen.append(body)
        if http_request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 7})
        if result_kind == "outage":
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        text = "invalid JSON" if result_kind == "malformed" else candidate.model_dump_json()
        return httpx.Response(200, json=envelope([{"text": text}]))
    async def check(t):
        provider = ModelReasoningProvider(t, "fixture-model", Limits(), audit_factory=lambda r: expected.append(project_diagnosis_request(r)) or service.make_audit(r.run.run_id, secrets=("fixture-secret",)))
        diagnosis = await DiagnosisService(service).diagnose(run.run_id, provider=provider)
        assert diagnosis.status == {"valid": DiagnosisStatus.SUPPORTED, "ungrounded": DiagnosisStatus.REJECTED, "malformed": DiagnosisStatus.INCONCLUSIVE, "outage": DiagnosisStatus.INCONCLUSIVE}[result_kind]
        assert run.model_execution["diagnostic"]["provider"] == "gemini"
        if result_kind == "outage":
            assert run.model_execution["diagnostic"]["failure_kind"] == "provider_harness"
        assert "fixture-secret" not in run.model_dump_json()
    execute(handler, check)
    assert "HIDDEN_TRUTH_NOT_SENT" not in repr(seen)
    for payload in (seen[0]["generateContentRequest"], seen[1]):
        assert "tools" not in payload
        assert json.loads(payload["contents"][0]["parts"][0]["text"]) == expected[0]


@pytest.mark.parametrize("perturbation", [PerturbationKind.NONE, PerturbationKind.FILE_MOVED])
def test_diagnostic_generation_503_preserves_task_facts_and_invalidates_comparison(perturbation):
    service = RunService()
    baseline, run = service.execute(perturbation), service.execute(perturbation)
    status, score = run.status, run.score
    validations = [v.model_dump(mode="json") for v in run.validations]
    diagnosis_service = DiagnosisService(service)
    requests = []
    def handler(req):
        requests.append(req.url.path)
        if req.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 2368})
        return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
    async def check(t):
        provider = ModelReasoningProvider(t, "fixture-model", Limits(), audit_factory=lambda r: service.make_audit(r.run.run_id))
        diagnosis = await diagnosis_service.diagnose(run.run_id, provider=provider)
        assert diagnosis.status == DiagnosisStatus.INCONCLUSIVE
        assert diagnosis.hypotheses == []
        assert diagnosis.validation_errors == ["provider:provider_harness:provider_http_503"]
    execute(handler, check)
    assert requests == ["/v1beta/models/fixture-model:countTokens", "/v1beta/models/fixture-model:generateContent"]
    assert (run.status, run.score) == (status, score)
    assert [v.model_dump(mode="json") for v in run.validations] == validations
    metadata = run.model_execution["diagnostic"]
    assert metadata["failure_kind"] == "provider_harness"
    assert metadata["accounting"]["provider_requests"] == 2
    assert metadata["accounting"]["usage_unknown"] is True
    repair = RepairService(service, diagnosis_service)
    vault = RegressionVaultService(service, repair)
    case = vault.register_case(baseline.run_id, name="diagnostic outage")
    comparison = repair.compare(baseline, run)
    assert comparison.comparable
    pair = vault._pair_result(type("Member", (), {"role": "historical"})(), case, baseline, run, comparison)
    assert pair.valid_pair is False
    assert pair.transition == PairTransition.INVALID
    assert "provider/harness failure is not a task regression" in pair.reasons
