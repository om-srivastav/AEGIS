from __future__ import annotations

from app.models.schemas import Diagnosis, DiagnosisCandidate, DiagnosisStatus, EventType
from app.providers.reasoning import DiagnosisRequest, MockProvider, ReasoningProvider
from app.providers.model_contracts import Fault
from app.services.evidence import FactExtractor
from app.services.runner import RunService


class DiagnosisValidationError(ValueError):
    pass


class DiagnosisValidator:
    def validate(self, request: DiagnosisRequest, candidate: DiagnosisCandidate) -> list[str]:
        errors: list[str] = []
        run = request.run
        events = {e.event_id: e for e in run.events}
        facts = {f.fact_id: f for f in request.evidence.facts}
        observations = {o.observation_id: o for o in request.evidence.observations}

        if candidate.selected_hypothesis_id and candidate.selected_hypothesis_id not in {
            h.hypothesis_id for h in candidate.hypotheses
        }:
            errors.append("selected_hypothesis_id does not reference a supplied hypothesis")

        for event_id in ([candidate.earliest_candidate_event_id] if candidate.earliest_candidate_event_id else []) + candidate.downstream_event_ids:
            if event_id not in request.allowed_event_ids or event_id not in events:
                errors.append(f"event reference outside allowed run evidence: {event_id}")

        for h in candidate.hypotheses:
            for event_id in h.candidate_event_ids:
                if event_id not in request.allowed_event_ids or event_id not in events:
                    errors.append(f"candidate event outside allowed run evidence: {event_id}")
            for ref in h.supporting_evidence + h.contradicting_evidence:
                if ref.run_id != run.run_id:
                    errors.append(f"wrong-run evidence reference: {ref.run_id}")
                    continue
                if ref.event_id not in request.allowed_event_ids or ref.event_id not in events:
                    errors.append(f"nonexistent/unallowed event reference: {ref.event_id}")
                if ref.fact_id not in request.allowed_fact_ids or ref.fact_id not in facts:
                    errors.append(f"nonexistent/unallowed fact reference: {ref.fact_id}")
                    continue
                fact = facts[ref.fact_id]
                if fact.run_id != run.run_id:
                    errors.append(f"fact belongs to wrong run: {ref.fact_id}")
                if fact.source_event_ids and ref.event_id not in fact.source_event_ids:
                    errors.append(f"event {ref.event_id} does not support fact {ref.fact_id}")
                if ref.claimed_value is not None and ref.claimed_value != fact.value:
                    errors.append(f"claimed value contradicts verified fact {ref.fact_id}")
                actual_validation_ids = {v.validation_id for v in run.validations}
                for validation_id in fact.validation_ids:
                    if validation_id not in actual_validation_ids:
                        errors.append(f"fact {ref.fact_id} references nonexistent validation {validation_id}")

            if candidate.earliest_candidate_event_id:
                cand = events.get(candidate.earliest_candidate_event_id)
                for ref in h.supporting_evidence:
                    ev = events.get(ref.event_id)
                    if cand and ev and ev.sequence > cand.sequence and ref.relation.value == "supports":
                        # Later evidence can support the eventual outcome, but cannot be treated as information
                        # available at the candidate decision. In v1, keep hypothesis support causal-prefix safe.
                        errors.append(
                            f"supporting evidence event {ref.event_id} occurs after earliest candidate {cand.event_id}"
                        )

        # Absence facts require a complete trace.
        if not run.trace_complete:
            for h in candidate.hypotheses:
                for ref in h.supporting_evidence:
                    fact = facts.get(ref.fact_id)
                    if fact and fact.predicate.startswith("list_files_called_after"):
                        errors.append("absence claim requires a complete trace")

        return sorted(set(errors))


class DiagnosisService:
    def __init__(self, run_service: RunService, provider: ReasoningProvider | None = None) -> None:
        self.run_service = run_service
        self.provider = provider or MockProvider()
        self.extractor = FactExtractor()
        self.validator = DiagnosisValidator()
        self.diagnoses: dict[str, Diagnosis] = {}

    async def diagnose(self, run_id: str, provider: ReasoningProvider | None = None) -> Diagnosis:
        active_provider = provider or self.provider
        run = self.run_service.get(run_id)
        if not run:
            raise KeyError(run_id)
        evidence = self.extractor.extract(run)
        request = DiagnosisRequest(
            run=run,
            evidence=evidence,
            allowed_event_ids=frozenset(e.event_id for e in run.events),
            allowed_fact_ids=frozenset(f.fact_id for f in evidence.facts),
        )
        try:
            candidate = await active_provider.diagnose(request)
        except Fault as exc:
            metadata = getattr(active_provider, "last_metadata", None)
            if metadata is not None:
                current = dict(run.model_execution or {"schema_version": "1"})
                current["diagnostic"] = {**metadata, "validation_status": "provider_failure"}
                run.model_execution = current
            diagnosis = Diagnosis(
                run_id=run.run_id,
                trace_hash=run.trace_hash or "",
                observation_ids=[o.observation_id for o in evidence.observations],
                hypotheses=[],
                status=DiagnosisStatus.INCONCLUSIVE,
                provider=active_provider.name,
                validation_errors=[f"provider:{exc.kind}:{exc.code}"],
            )
            self.diagnoses[diagnosis.diagnosis_id] = diagnosis
            return diagnosis

        errors = self.validator.validate(request, candidate)
        status = (
            DiagnosisStatus.REJECTED if errors
            else DiagnosisStatus.SUPPORTED if candidate.hypotheses
            else DiagnosisStatus.INCONCLUSIVE
        )
        metadata = getattr(active_provider, "last_metadata", None)
        if metadata is not None:
            current = dict(run.model_execution or {"schema_version": "1"})
            current["diagnostic"] = {
                **metadata,
                "validation_status": "rejected" if errors else status.value,
            }
            run.model_execution = current
        diagnosis = Diagnosis(
            run_id=run.run_id,
            trace_hash=run.trace_hash or "",
            observation_ids=[o.observation_id for o in evidence.observations],
            hypotheses=[] if errors else candidate.hypotheses,
            selected_hypothesis_id=None if errors else candidate.selected_hypothesis_id,
            earliest_candidate_event_id=None if errors else candidate.earliest_candidate_event_id,
            downstream_event_ids=[] if errors else candidate.downstream_event_ids,
            probes=[] if errors else candidate.probes,
            status=status,
            provider=active_provider.name,
            validation_errors=errors,
        )
        self.diagnoses[diagnosis.diagnosis_id] = diagnosis
        return diagnosis

    def get(self, diagnosis_id: str) -> Diagnosis | None:
        return self.diagnoses.get(diagnosis_id)
