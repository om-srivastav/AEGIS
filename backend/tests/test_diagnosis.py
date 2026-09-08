import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    DiagnosisCandidate,
    EvidenceReference,
    EvidenceRelation,
    FailureCategory,
    PerturbationKind,
    RootCauseHypothesis,
)
from app.providers.reasoning import DiagnosisRequest, MockProvider
from app.services.diagnosis import DiagnosisService, DiagnosisValidator
from app.services.evidence import FactExtractor
from app.services.runner import RunService


def _request_for_moved_run():
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    evidence = FactExtractor().extract(run)
    req = DiagnosisRequest(
        run=run,
        evidence=evidence,
        allowed_event_ids=frozenset(e.event_id for e in run.events),
        allowed_fact_ids=frozenset(f.fact_id for f in evidence.facts),
    )
    return service, run, evidence, req


def test_moved_resource_produces_grounded_diagnosis():
    service, run, evidence, req = _request_for_moved_run()
    ds = DiagnosisService(service, MockProvider())
    diagnosis = asyncio.run(ds.diagnose(run.run_id))
    assert diagnosis.status.value == "supported"
    assert diagnosis.hypotheses
    assert diagnosis.earliest_candidate_event_id in {e.event_id for e in run.events}
    allowed_facts = {f.fact_id for f in evidence.facts}
    for ref in diagnosis.hypotheses[0].supporting_evidence:
        assert ref.fact_id in allowed_facts


def test_nonexistent_event_id_is_rejected():
    _, run, evidence, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    candidate.hypotheses[0].supporting_evidence[0].event_id = "missing-event"
    assert any("event" in e for e in DiagnosisValidator().validate(req, candidate))


def test_wrong_run_evidence_is_rejected():
    _, _, _, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    candidate.hypotheses[0].supporting_evidence[0].run_id = "another-run"
    assert any("wrong-run" in e for e in DiagnosisValidator().validate(req, candidate))


def test_nonexistent_fact_id_is_rejected():
    _, _, _, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    candidate.hypotheses[0].supporting_evidence[0].fact_id = "missing-fact"
    assert any("fact" in e for e in DiagnosisValidator().validate(req, candidate))


def test_fact_event_contradiction_is_rejected():
    _, run, _, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    unrelated = next(e for e in run.events if e.kind.value == "run_started")
    candidate.hypotheses[0].supporting_evidence[0].event_id = unrelated.event_id
    assert any("does not support fact" in e for e in DiagnosisValidator().validate(req, candidate))


def test_impossible_chronology_is_rejected():
    _, run, _, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    late_event = next(e for e in run.events if e.kind.value == "validation")
    ref = candidate.hypotheses[0].supporting_evidence[0]
    # Make a real fact source include a later event to isolate chronology validation.
    fact = next(f for f in req.evidence.facts if f.fact_id == ref.fact_id)
    fact.source_event_ids.append(late_event.event_id)
    ref.event_id = late_event.event_id
    assert any("occurs after earliest candidate" in e for e in DiagnosisValidator().validate(req, candidate))


def test_confidence_out_of_range_is_schema_rejected():
    _, run, evidence, req = _request_for_moved_run()
    error_fact = next(f for f in evidence.facts if f.predicate == "execution_error_type")
    with pytest.raises(ValidationError):
        RootCauseHypothesis(
            category=FailureCategory.RECOVERY,
            candidate_event_ids=[error_fact.source_event_ids[0]],
            mechanism="x",
            supporting_evidence=[
                EvidenceReference(
                    run_id=run.run_id,
                    event_id=error_fact.source_event_ids[0],
                    fact_id=error_fact.fact_id,
                    relation=EvidenceRelation.SUPPORTS,
                )
            ],
            confidence=1.5,
            confidence_basis="invalid",
        )


def test_malformed_diagnosis_candidate_is_schema_rejected():
    with pytest.raises(ValidationError):
        DiagnosisCandidate.model_validate({"hypotheses": "not-a-list"})


def test_absence_claim_requires_complete_trace():
    _, run, evidence, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    run.trace_complete = False
    errors = DiagnosisValidator().validate(req, candidate)
    assert "absence claim requires a complete trace" in errors


def test_nonexistent_validation_reference_is_rejected():
    _, run, evidence, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    ref = candidate.hypotheses[0].supporting_evidence[0]
    fact = next(f for f in evidence.facts if f.fact_id == ref.fact_id)
    fact.validation_ids = ["missing-validation"]
    errors = DiagnosisValidator().validate(req, candidate)
    assert any("nonexistent validation" in e for e in errors)


def test_claimed_fact_value_contradiction_is_rejected():
    _, _, _, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    candidate.hypotheses[0].supporting_evidence[0].claimed_value = "NotTheRecordedValue"
    errors = DiagnosisValidator().validate(req, candidate)
    assert any("contradicts verified fact" in e for e in errors)


def test_provider_evidence_outside_allowed_bundle_is_rejected():
    _, _, _, req = _request_for_moved_run()
    candidate = asyncio.run(MockProvider().diagnose(req))
    ref = candidate.hypotheses[0].supporting_evidence[0]
    req2 = DiagnosisRequest(
        run=req.run,
        evidence=req.evidence,
        allowed_event_ids=req.allowed_event_ids,
        allowed_fact_ids=frozenset(x for x in req.allowed_fact_ids if x != ref.fact_id),
    )
    assert any("unallowed fact" in e for e in DiagnosisValidator().validate(req2, candidate))
