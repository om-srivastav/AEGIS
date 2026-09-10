import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

import pytest

from app.models.schemas import EventType, PerturbationKind, RunStatus
from app.providers.gemini_transport import GeminiTransport
from app.providers.model_config import build_transport, read_model_config
from app.providers.model_contracts import Audit
from app.providers.model_reasoning import ModelReasoningProvider
from app.services.diagnosis import DiagnosisService
from app.services.runner import RunService

pytestmark = pytest.mark.live


def require_live_role(prefix: str) -> None:
    if os.environ.get("AEGIS_RUN_LIVE_TESTS") != "1":
        pytest.skip("live model tests are disabled")
    required = (f"{prefix}_PROVIDER", f"{prefix}_MODEL", f"{prefix}_API_KEY")
    if any(not os.environ.get(name) for name in required):
        pytest.fail("live provider configuration is incomplete", pytrace=False)


def save_evidence(tmp_path, label, payload, secrets):
    directory = Path(os.environ.get("AEGIS_LIVE_OUTPUT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    # Existing trace redaction plus a final boundary for the exported evidence.
    audit = Audit(lambda event, data: (directory / f"{label}.json").write_text(json.dumps(data, indent=2), encoding="utf-8"), secrets=secrets)
    audit("live.evidence", **payload)


async def checked_transport(stack, config, tmp_path, label):
    transport = build_transport(config)
    stack.push_async_callback(transport.aclose)
    if isinstance(transport, GeminiTransport):
        events = []
        try:
            await transport.verify_model(config.model, config.limits.request_seconds, Audit(lambda k, p: events.append({"event": k, **p}), secrets=(config.api_key,)))
        finally:
            save_evidence(tmp_path, f"{label}-model-check", {"events": events}, (config.api_key,))
    return transport


def assert_agent(run, config):
    metadata = run.model_execution["agent"]
    assert metadata["failure_kind"] is None, metadata.get("failure_code")
    assert metadata["provider"] == config.provider
    assert metadata["requested_model"] == config.model
    assert metadata["resolved_models"]
    assert metadata["report_artifact_id"]
    assert metadata["accounting"]["usage_unknown"] is False
    assert metadata["accounting"]["provider_requests"] > 0
    assert run.status == RunStatus.PASSED
    assert all(v.passed for v in run.validations)
    assert next(v for v in run.validations if v.name == "report_values").actual is not None
    dispatched = [e.payload["tool"] for e in run.events if e.message == "agent.tool.dispatched"]
    assert "write_report" in dispatched
    assert set(dispatched) <= {"list_files", "read_file", "write_report"}
    assert any(e.kind == EventType.TOOL_CALL and e.message == "write_report" for e in run.events)
    assert any(e.message == "model.response" and e.payload["response"]["response_id"] for e in run.events)
    assert run.trace_complete and run.trace_hash


def assert_diagnosis(run, diagnosis, config):
    metadata = run.model_execution["diagnostic"]
    assert metadata["provider"] == config.provider
    assert metadata["requested_model"] == config.model
    assert metadata.get("failure_kind") is None, metadata.get("failure_code")
    assert metadata.get("resolved_models")
    # Empty, grounded hypotheses may validly be inconclusive. Provider failures
    # and rejected grounding must never pass merely because the adapter ran.
    assert metadata["validation_status"] in ("supported", "inconclusive")
    assert not diagnosis.validation_errors
    assert metadata["accounting"]["usage_unknown"] is False


def test_live_model_agent_sales_report(tmp_path):
    require_live_role("AEGIS_AUT")
    config = read_model_config("AEGIS_AUT")
    async def run_live():
        async with AsyncExitStack() as stack:
            transport = await checked_transport(stack, config, tmp_path, "agent")
            run = await RunService().execute_model(PerturbationKind.NONE, transport=transport, model=config.model, limits=config.limits, secrets=(config.api_key,))
            save_evidence(tmp_path, f"agent-{run.run_id}", {"run": run.model_dump(mode="json")}, (config.api_key,))
            assert_agent(run, config)
    asyncio.run(run_live())


def test_live_model_diagnosis_remains_grounded(tmp_path):
    require_live_role("AEGIS_DIAG")
    config = read_model_config("AEGIS_DIAG")
    async def run_live():
        async with AsyncExitStack() as stack:
            transport = await checked_transport(stack, config, tmp_path, "diagnostic")
            service = RunService()
            run = service.execute(PerturbationKind.FILE_MOVED)
            provider = ModelReasoningProvider(transport, config.model, config.limits, audit_factory=lambda r: service.make_audit(r.run.run_id, secrets=(config.api_key,)))
            diagnosis = await DiagnosisService(service).diagnose(run.run_id, provider=provider)
            save_evidence(tmp_path, f"diagnostic-{run.run_id}", {"run": run.model_dump(mode="json"), "diagnosis": diagnosis.model_dump(mode="json")}, (config.api_key,))
            assert_diagnosis(run, diagnosis, config)
    asyncio.run(run_live())


def test_live_combined_agent_and_diagnosis(tmp_path):
    require_live_role("AEGIS_AUT")
    require_live_role("AEGIS_DIAG")
    if os.environ.get("AEGIS_RUN_COMBINED_LIVE_TEST") != "1":
        pytest.skip("combined run requires a separate opt-in after individual checks pass")
    agent, diagnostic = read_model_config("AEGIS_AUT"), read_model_config("AEGIS_DIAG")
    secrets = (agent.api_key, diagnostic.api_key)
    async def run_live():
        async with AsyncExitStack() as stack:
            aut_transport = await checked_transport(stack, agent, tmp_path, "combined-agent")
            diag_transport = await checked_transport(stack, diagnostic, tmp_path, "combined-diagnostic")
            service = RunService()
            run = await service.execute_model(PerturbationKind.NONE, transport=aut_transport, model=agent.model, limits=agent.limits, secrets=secrets)
            save_evidence(tmp_path, f"combined-agent-{run.run_id}", {"run": run.model_dump(mode="json")}, secrets)
            assert_agent(run, agent)
            provider = ModelReasoningProvider(diag_transport, diagnostic.model, diagnostic.limits, audit_factory=lambda r: service.make_audit(r.run.run_id, secrets=secrets))
            diagnosis = await DiagnosisService(service).diagnose(run.run_id, provider=provider)
            save_evidence(tmp_path, f"combined-{run.run_id}", {"run": run.model_dump(mode="json"), "diagnosis": diagnosis.model_dump(mode="json")}, secrets)
            assert_diagnosis(run, diagnosis, diagnostic)
    asyncio.run(run_live())
