from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from app.models.schemas import (
    ActivationSummary,
    AgentPolicy,
    DiagnosisStatus,
    EvaluationRecord,
    GateDecision,
    InvariantCheck,
    PerturbationKind,
    RecoverySummary,
    RepairApplicability,
    RepairProposal,
    ResourceReobservationPolicy,
    RunComparison,
    RunRecord,
    RunStatus,
)
from app.services.diagnosis import DiagnosisService
from app.services.policy import compute_policy_hash, make_policy
from app.services.runner import RunService


class RepairValidationError(ValueError):
    pass


class RepairService:
    def __init__(self, run_service: RunService, diagnosis_service: DiagnosisService) -> None:
        self.run_service = run_service
        self.diagnosis_service = diagnosis_service
        self.repairs: dict[str, RepairProposal] = {}
        self.evaluations: dict[str, EvaluationRecord] = {}
        self.idempotency: dict[str, str] = {}

    def propose(self, diagnosis_id: str) -> RepairProposal:
        diagnosis = self.diagnosis_service.get(diagnosis_id)
        if not diagnosis:
            raise KeyError(diagnosis_id)
        if diagnosis.status != DiagnosisStatus.SUPPORTED or not diagnosis.hypotheses:
            raise RepairValidationError("repair requires a supported diagnosis")
        run = self.run_service.get(diagnosis.run_id)
        if not run or not run.effective_policy_id:
            raise RepairValidationError("source run or policy unavailable")
        base_policy = self.run_service.policies[run.effective_policy_id]
        old_cfg = base_policy.resource_reobservation
        if old_cfg.enabled:
            raise RepairValidationError("source policy already has re-observation enabled")
        new_cfg = old_cfg.model_copy(update={"enabled": True})
        hypothesis = next((h for h in diagnosis.hypotheses if h.hypothesis_id == diagnosis.selected_hypothesis_id), diagnosis.hypotheses[0])
        proposal = RepairProposal(
            diagnosis_id=diagnosis.diagnosis_id,
            source_run_id=run.run_id,
            base_policy_id=base_policy.policy_id,
            base_policy_hash=base_policy.policy_hash,
            old_configuration=old_cfg,
            new_configuration=new_cfg,
            supporting_evidence=list(hypothesis.supporting_evidence),
            applicability=RepairApplicability(
                task_id=run.task.task_id,
                task_version=run.task.version,
                agent_version=run.scenario.agent_version if run.scenario else "",
                resource_id=run.task.resource.resource_id,
            ),
            evaluation_plan_id="mission03-two-case-v1",
        )
        errors = self.validate_proposal(proposal)
        if errors:
            proposal = proposal.model_copy(update={"status": "rejected", "validation_errors": errors})
        self.repairs[proposal.repair_id] = proposal
        return proposal

    def validate_proposal(self, proposal: RepairProposal) -> list[str]:
        errors: list[str] = []
        run = self.run_service.get(proposal.source_run_id)
        diagnosis = self.diagnosis_service.get(proposal.diagnosis_id)
        base = self.run_service.policies.get(proposal.base_policy_id)
        if not run or not diagnosis or not base:
            return ["source records unavailable"]
        if diagnosis.run_id != run.run_id:
            errors.append("diagnosis/source run mismatch")
        if base.policy_hash != proposal.base_policy_hash:
            errors.append("base policy hash mismatch")
        if proposal.old_configuration != base.resource_reobservation:
            errors.append("old configuration does not match base policy")
        old = proposal.old_configuration.model_dump()
        new = proposal.new_configuration.model_dump()
        diffs = {k for k in old if old[k] != new[k]}
        if diffs != {"enabled"} or old["enabled"] is not False or new["enabled"] is not True:
            errors.append("only enabled false->true may change in Mission 03")
        if proposal.applicability.task_id != run.task.task_id or proposal.applicability.task_version != run.task.version:
            errors.append("task applicability mismatch")
        if proposal.applicability.resource_id != run.task.resource.resource_id:
            errors.append("resource identity must come from task contract")
        if proposal.applicability.agent_version != (run.scenario.agent_version if run.scenario else ""):
            errors.append("agent version mismatch")
        valid_facts = set()
        valid_events = {e.event_id for e in run.events}
        for ref in proposal.supporting_evidence:
            if ref.run_id != run.run_id or ref.event_id not in valid_events:
                errors.append("invalid supporting evidence")
        return sorted(set(errors))

    def _candidate_policy(self, proposal: RepairProposal) -> AgentPolicy:
        base = self.run_service.policies[proposal.base_policy_id]
        candidate = AgentPolicy(
            version=base.version + 1,
            parent_policy_id=base.policy_id,
            resource_reobservation=proposal.new_configuration,
        )
        candidate = candidate.model_copy(update={"policy_hash": compute_policy_hash(candidate)})
        self.run_service.register_policy(candidate)
        return candidate

    def evaluate(self, repair_id: str, *, idempotency_key: str | None = None) -> EvaluationRecord:
        if idempotency_key and idempotency_key in self.idempotency:
            return self.evaluations[self.idempotency[idempotency_key]]
        proposal = self.repairs.get(repair_id)
        if not proposal:
            raise KeyError(repair_id)
        errors = self.validate_proposal(proposal)
        if errors or proposal.status != "valid":
            raise RepairValidationError("invalid repair proposal: " + "; ".join(errors or proposal.validation_errors))
        base = self.run_service.policies[proposal.base_policy_id]
        candidate = self._candidate_policy(proposal)
        evaluation_id = str(uuid4())

        moved_baseline = self.run_service.execute(
            PerturbationKind.FILE_MOVED, policy=base, replay_of_run_id=proposal.source_run_id,
            evaluation_id=evaluation_id, evaluation_case="moved_resource", evaluation_arm="baseline"
        )
        moved_candidate = self.run_service.execute(
            PerturbationKind.FILE_MOVED, policy=candidate, replay_of_run_id=proposal.source_run_id,
            evaluation_id=evaluation_id, evaluation_case="moved_resource", evaluation_arm="candidate"
        )
        clean_baseline = self.run_service.execute(
            PerturbationKind.NONE, policy=base, replay_of_run_id=proposal.source_run_id,
            evaluation_id=evaluation_id, evaluation_case="clean", evaluation_arm="baseline"
        )
        clean_candidate = self.run_service.execute(
            PerturbationKind.NONE, policy=candidate, replay_of_run_id=proposal.source_run_id,
            evaluation_id=evaluation_id, evaluation_case="clean", evaluation_arm="candidate"
        )

        moved_cmp = self.compare(moved_baseline, moved_candidate)
        clean_cmp = self.compare(clean_baseline, clean_candidate)
        decision, reasons = self._gate(moved_baseline, moved_candidate, clean_baseline, clean_candidate, moved_cmp, clean_cmp)
        statement = (
            "Enabling the bounded resource re-observation policy changed the moved-resource scenario "
            "from failure to success under matched deterministic conditions; the clean case remained correct."
            if decision == GateDecision.ACCEPT else
            "Mission 03 did not establish an accepted intervention-supported policy effect."
        )
        record = EvaluationRecord(
            evaluation_id=evaluation_id,
            repair_id=repair_id,
            baseline_policy_id=base.policy_id,
            candidate_policy_id=candidate.policy_id,
            moved_baseline_run_id=moved_baseline.run_id,
            moved_candidate_run_id=moved_candidate.run_id,
            clean_baseline_run_id=clean_baseline.run_id,
            clean_candidate_run_id=clean_candidate.run_id,
            moved_comparison=moved_cmp,
            clean_comparison=clean_cmp,
            decision=decision,
            reasons=reasons,
            causal_statement=statement,
        )
        self.evaluations[evaluation_id] = record
        if idempotency_key:
            self.idempotency[idempotency_key] = evaluation_id
        return record

    def _activation(self, run: RunRecord) -> ActivationSummary:
        events = [e for e in run.events if e.kind.value == "perturbation" and e.message == "file_moved_activated"]
        exposure = False
        if events:
            act_seq = events[0].sequence
            failed = [e for e in run.events if e.kind.value == "tool_result" and e.payload.get("error_code") == "FILE_NOT_FOUND"]
            exposure = bool(failed and failed[0].sequence > act_seq)
        return ActivationSummary(activated=bool(events), count=len(events), exposure_verified=exposure)

    def _recovery(self, run: RunRecord) -> RecoverySummary:
        rec = [e for e in run.events if e.kind.value == "recovery"]
        return RecoverySummary(
            attempted=bool(rec),
            discovery_calls=sum(1 for e in run.events if e.kind.value == "tool_call" and e.message == "list_files"),
            recovery_read_calls=sum(1 for e in run.events if e.kind.value == "tool_call" and e.message == "read_file" and e.payload.get("recovery")),
            terminal_status=next((e.payload.get("terminal_status") for e in reversed(rec) if e.payload.get("terminal_status")), None),
        )

    def compare(self, baseline: RunRecord, candidate: RunRecord) -> RunComparison:
        checks = []
        fields = [
            ("task", baseline.scenario.task_contract_hash, candidate.scenario.task_contract_hash),
            ("fixture", baseline.scenario.fixture_hash, candidate.scenario.fixture_hash),
            ("perturbation", baseline.scenario.perturbation.value, candidate.scenario.perturbation.value),
            ("validator", baseline.scenario.validator_hash, candidate.scenario.validator_hash),
            ("agent", baseline.scenario.agent_version, candidate.scenario.agent_version),
            ("environment", baseline.scenario.environment_version, candidate.scenario.environment_version),
            ("limits", baseline.scenario.max_steps, candidate.scenario.max_steps),
        ]
        for name, a, b in fields:
            checks.append(InvariantCheck(name=name, passed=a == b, details=f"{a} == {b}"))
        comparable = all(c.passed for c in checks)
        reasons = [c.name for c in checks if not c.passed]
        bvals = {v.name: v.passed for v in baseline.validations}
        cvals = {v.name: v.passed for v in candidate.validations}
        diffs = [
            {"check_id": k, "baseline": bvals.get(k), "candidate": cvals.get(k)}
            for k in sorted(set(bvals) | set(cvals)) if bvals.get(k) != cvals.get(k)
        ]
        btools = sum(1 for e in baseline.events if e.kind.value == "tool_call")
        ctools = sum(1 for e in candidate.events if e.kind.value == "tool_call")
        bactive = self._activation(baseline)
        cactive = self._activation(candidate)
        required_pass = all(v.passed for v in candidate.validations)
        resolved = None if not comparable else (baseline.status == RunStatus.FAILED and candidate.status == RunStatus.PASSED and required_pass)
        return RunComparison(
            baseline_run_id=baseline.run_id,
            candidate_run_id=candidate.run_id,
            invariant_checks=checks,
            comparable=comparable,
            noncomparability_reasons=reasons,
            baseline_outcome=baseline.status,
            candidate_outcome=candidate.status,
            baseline_activation=bactive,
            candidate_activation=cactive,
            policy_diff={"resource_reobservation.enabled": [False, True]},
            validation_differences=diffs,
            recovery_summary=self._recovery(candidate),
            baseline_tool_call_count=btools,
            candidate_tool_call_count=ctools,
            tool_call_count_delta=ctools - btools,
            original_failure_resolved=resolved,
        )

    def _gate(self, mb, mc, cb, cc, moved_cmp, clean_cmp):
        reasons: list[str] = []
        if not moved_cmp.comparable or not clean_cmp.comparable:
            return GateDecision.INCONCLUSIVE, ["manifest invariant mismatch"]
        if mb.status != RunStatus.FAILED:
            return GateDecision.INCONCLUSIVE, ["moved baseline did not reproduce expected failure"]
        if cb.status != RunStatus.PASSED:
            return GateDecision.INCONCLUSIVE, ["clean baseline failed"]
        if not moved_cmp.baseline_activation.activated or not moved_cmp.candidate_activation.activated:
            return GateDecision.INCONCLUSIVE, ["moved-resource perturbation not activated in both arms"]
        if not moved_cmp.baseline_activation.exposure_verified or not moved_cmp.candidate_activation.exposure_verified:
            return GateDecision.INCONCLUSIVE, ["missing-resource exposure not verified in both arms"]
        if mc.status != RunStatus.PASSED:
            return GateDecision.REJECT, ["moved candidate still failed"]
        if cc.status != RunStatus.PASSED:
            return GateDecision.REJECT, ["clean candidate regressed"]
        if clean_cmp.recovery_summary.attempted:
            return GateDecision.REJECT, ["recovery executed on clean case without trigger"]
        rec = moved_cmp.recovery_summary
        if not rec.attempted or rec.discovery_calls != 1 or rec.recovery_read_calls != 1 or rec.terminal_status != "success":
            return GateDecision.REJECT, ["bounded recovery contract not satisfied"]
        return GateDecision.ACCEPT, ["passed Mission 03 two-case deterministic gate"]

    def get_repair(self, repair_id: str):
        return self.repairs.get(repair_id)

    def get_evaluation(self, evaluation_id: str):
        return self.evaluations.get(evaluation_id)
