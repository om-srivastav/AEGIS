import asyncio

from app.models.schemas import DiagnosisStatus, EventType, PerturbationKind
from app.providers.model_contracts import Limits
from app.providers.model_reasoning import ModelReasoningProvider
from app.providers.reasoning import DiagnosisRequest, MockProvider
from app.services.diagnosis import DiagnosisService
from app.services.evidence import FactExtractor
from app.services.runner import RunService

from .fakes import ScriptedTransport, final_json


def _request(run):
    evidence = FactExtractor().extract(run)
    return DiagnosisRequest(
        run=run,
        evidence=evidence,
        allowed_event_ids=frozenset(e.event_id for e in run.events),
        allowed_fact_ids=frozenset(f.fact_id for f in evidence.facts),
    )


def test_real_mission04_diagnosis_schema_round_trip_and_hidden_truth_boundary():
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    run.scenario.fixture_snapshot["hidden-secret.txt"] = "MUST_NOT_BE_SENT"
    request = _request(run)
    candidate = asyncio.run(MockProvider().diagnose(request))

    transport = ScriptedTransport([final_json(candidate.model_dump_json())])
    provider = ModelReasoningProvider(
        transport,
        "offline-fixture-model",
        Limits(max_steps=1, max_provider_requests=2),
        audit_factory=lambda req: service.make_audit(req.run.run_id),
    )
    diagnosis_service = DiagnosisService(service)
    diagnosis = asyncio.run(diagnosis_service.diagnose(run.run_id, provider=provider))

    assert diagnosis.status == DiagnosisStatus.SUPPORTED
    assert diagnosis.selected_hypothesis_id == candidate.selected_hypothesis_id
    assert transport.requests[0].tools == ()
    assert "MUST_NOT_BE_SENT" not in repr(transport.requests)
    assert "MUST_NOT_BE_SENT" not in repr(transport.count_requests)
    assert any(e.kind == EventType.MODEL and e.message == "diagnosis.model.started" for e in run.events)
    assert run.model_execution["diagnostic"]["validation_status"] == "supported"


def test_existing_diagnosis_validator_rejects_unknown_evidence_from_model():
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    request = _request(run)
    candidate = asyncio.run(MockProvider().diagnose(request))
    bad = candidate.model_copy(deep=True)
    ref = bad.hypotheses[0].supporting_evidence[0]
    bad.hypotheses[0].supporting_evidence[0] = ref.model_copy(update={"event_id": "missing-event"})

    transport = ScriptedTransport([final_json(bad.model_dump_json())])
    provider = ModelReasoningProvider(
        transport,
        "offline-fixture-model",
        Limits(max_steps=1, max_provider_requests=2),
    )
    diagnosis_service = DiagnosisService(service)
    diagnosis = asyncio.run(diagnosis_service.diagnose(run.run_id, provider=provider))
    assert diagnosis.status == DiagnosisStatus.REJECTED
    assert any("nonexistent/unallowed event reference" in err for err in diagnosis.validation_errors)


def test_malformed_model_diagnosis_becomes_inconclusive_provider_result():
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    transport = ScriptedTransport([final_json("```json\n{}\n```")])
    provider = ModelReasoningProvider(
        transport,
        "offline-fixture-model",
        Limits(max_steps=1, max_provider_requests=2),
    )
    diagnosis = asyncio.run(DiagnosisService(service).diagnose(run.run_id, provider=provider))
    assert diagnosis.status == DiagnosisStatus.INCONCLUSIVE
    assert diagnosis.validation_errors == ["provider:model:malformed_diagnosis_json"]
