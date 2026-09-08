from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models.schemas import (
    DiagnosisCandidate,
    DiagnosticProbe,
    EvidenceReference,
    EvidenceRelation,
    FailureCategory,
    RootCauseHypothesis,
    RunRecord,
    VerifiedFact,
)
from app.services.evidence import EvidenceBundle


@dataclass(frozen=True)
class DiagnosisRequest:
    run: RunRecord
    evidence: EvidenceBundle
    allowed_event_ids: frozenset[str]
    allowed_fact_ids: frozenset[str]


class ReasoningProvider(Protocol):
    name: str

    async def diagnose(self, request: DiagnosisRequest) -> DiagnosisCandidate: ...


class MockProvider:
    """Deterministic scripted reasoning used only to test grounding machinery."""

    name = "mock-scripted-v1"

    async def diagnose(self, request: DiagnosisRequest) -> DiagnosisCandidate:
        run = request.run
        if run.perturbation.value != "file_moved":
            return DiagnosisCandidate()

        error_fact = next(
            (f for f in request.evidence.facts if f.predicate == "execution_error_type" and f.value == "FileNotFoundError"),
            None,
        )
        recovery_fact = next(
            (f for f in request.evidence.facts if f.predicate == "list_files_called_after_file_not_found"),
            None,
        )
        if not error_fact or not recovery_fact or recovery_fact.value is not False:
            return DiagnosisCandidate()

        error_event_id = error_fact.source_event_ids[0]
        candidate = RootCauseHypothesis(
            category=FailureCategory.RECOVERY,
            candidate_event_ids=[error_event_id],
            mechanism=(
                "After the direct read failed, the observed trajectory ended without a file-discovery "
                "re-observation before deterministic validation failed."
            ),
            supporting_evidence=[
                EvidenceReference(
                    run_id=run.run_id,
                    event_id=error_event_id,
                    fact_id=error_fact.fact_id,
                    relation=EvidenceRelation.SUPPORTS,
                    claimed_value=error_fact.value,
                ),
                EvidenceReference(
                    run_id=run.run_id,
                    event_id=error_event_id,
                    fact_id=recovery_fact.fact_id,
                    relation=EvidenceRelation.SUPPORTS,
                    claimed_value=recovery_fact.value,
                ),
            ],
            confidence=0.85,
            confidence_basis="Two deterministic facts support the observable recovery failure pattern.",
            limitations=["This is not yet intervention-supported causal evidence."],
        )
        first_validation_failure = next(
            (e.event_id for e in run.events if e.kind.value == "validation" and e.payload.get("passed") is False),
            None,
        )
        return DiagnosisCandidate(
            hypotheses=[candidate],
            selected_hypothesis_id=candidate.hypothesis_id,
            earliest_candidate_event_id=error_event_id,
            downstream_event_ids=[first_validation_failure] if first_validation_failure else [],
            probes=[
                DiagnosticProbe(
                    kind="reobserve_state",
                    parameters={"tool": "list_files"},
                    expected_discriminating_result="A fresh run can discover the moved resource before fallback/failure.",
                )
            ],
        )
