from __future__ import annotations

import hashlib
import json

from app.models.schemas import (
    EventType,
    FailureKind,
    FailureObservation,
    PerturbationKind,
    RunRecord,
    VerifiedFact,
)


class EvidenceBundle:
    def __init__(self, facts: list[VerifiedFact], observations: list[FailureObservation]):
        self.facts = facts
        self.observations = observations


class FactExtractor:
    version = "fact-extractor-v1"

    def extract(self, run: RunRecord) -> EvidenceBundle:
        facts: list[VerifiedFact] = []

        def add(predicate: str, value, *, operands=None, events=None, validations=None):
            operands = operands or {}
            events = events or []
            validations = validations or []
            identity = json.dumps({
                "run_id": run.run_id,
                "predicate": predicate,
                "operands": operands,
                "value": value,
                "events": events,
                "validations": validations,
            }, sort_keys=True, default=str).encode()
            fact_id = "fact_" + hashlib.sha256(identity).hexdigest()[:24]
            facts.append(
                VerifiedFact(
                    fact_id=fact_id,
                    run_id=run.run_id,
                    predicate=predicate,
                    operands=operands,
                    value=value,
                    source_event_ids=events,
                    validation_ids=validations,
                )
            )

        perturbation_events = [e for e in run.events if e.kind == EventType.PERTURBATION]
        add(
            "perturbation_activated",
            run.perturbation != PerturbationKind.NONE,
            operands={"kind": run.perturbation.value},
            events=[e.event_id for e in perturbation_events],
        )

        exception_events = [
            e for e in run.events
            if e.kind == EventType.OBSERVATION and e.payload.get("error_type")
        ]
        for e in exception_events:
            add(
                "execution_error_type",
                e.payload.get("error_type"),
                operands={"message": e.payload.get("error", "")},
                events=[e.event_id],
            )

        # Absence facts are only extracted from finalized/complete traces.
        if run.trace_complete:
            file_error_events = [
                e for e in exception_events if e.payload.get("error_type") == "FileNotFoundError"
            ]
            if file_error_events:
                err_seq = file_error_events[0].sequence
                later_list_calls = [
                    e for e in run.events
                    if e.sequence > err_seq
                    and e.kind == EventType.TOOL_CALL
                    and e.message == "list_files"
                ]
                add(
                    "list_files_called_after_file_not_found",
                    bool(later_list_calls),
                    operands={"after_event_id": file_error_events[0].event_id},
                    events=[file_error_events[0].event_id] + [e.event_id for e in later_list_calls],
                )

        for v in run.validations:
            add(
                "validation_passed",
                v.passed,
                operands={"name": v.name},
                validations=[v.validation_id],
                events=v.evidence_event_ids,
            )

        observations: list[FailureObservation] = []
        if run.status.value == "failed":
            failed_validations = [v for v in run.validations if not v.passed]
            detected = next(
                (e for e in run.events if e.kind == EventType.VALIDATION and not e.payload.get("passed", True)),
                run.events[-1],
            )
            kind = FailureKind.EXECUTION_FAILURE if exception_events else FailureKind.INCORRECT_OUTPUT
            related_fact_ids = [
                f.fact_id for f in facts
                if (f.validation_ids and any(v.validation_id in f.validation_ids for v in failed_validations))
                or f.predicate in {"execution_error_type", "list_files_called_after_file_not_found"}
            ]
            observations.append(
                FailureObservation(
                    run_id=run.run_id,
                    kind=kind,
                    detected_at_event_id=detected.event_id,
                    validation_ids=[v.validation_id for v in failed_validations],
                    fact_ids=related_fact_ids,
                    summary="Deterministic validation detected a failed sales-report run.",
                )
            )

        return EvidenceBundle(facts, observations)
