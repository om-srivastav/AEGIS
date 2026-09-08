from __future__ import annotations

from uuid import uuid4

from app.models.schemas import (
    CaseOutcome,
    CasePairResult,
    EvaluationRecord,
    EvaluationSuiteSnapshot,
    ExpectedCheck,
    ExposureRequirement,
    GateDecision,
    PairTransition,
    PerturbationKind,
    RegressionCase,
    ReliabilityMetrics,
    RunStatus,
    SuiteMember,
)
from app.services.policy import stable_hash
from app.services.repair import RepairService, RepairValidationError
from app.services.runner import RunService


class RegressionValidationError(ValueError):
    pass


class RegressionVaultService:
    """Mission 04 immutable regression-case vault and deterministic suite evaluator."""

    def __init__(self, run_service: RunService, repair_service: RepairService) -> None:
        self.run_service = run_service
        self.repair_service = repair_service
        self.cases: dict[str, RegressionCase] = {}
        self.case_versions: dict[tuple[str, int], RegressionCase] = {}
        self.case_fingerprints: dict[str, tuple[str, int]] = {}
        self.suites: dict[str, EvaluationSuiteSnapshot] = {}
        self.idempotency: dict[str, str] = {}

    @staticmethod
    def _manifest_hash(run) -> str:
        if not run.scenario:
            raise RegressionValidationError("source run has no scenario manifest")
        data = run.scenario.model_dump(mode="json")
        # A regression case pins scenario semantics, not either evaluation-arm policy.
        data.pop("policy_version", None)
        data.pop("policy_hash", None)
        return stable_hash(data)

    @staticmethod
    def _execution_contract_hash(run) -> str:
        s = run.scenario
        if not s:
            raise RegressionValidationError("source run has no scenario manifest")
        return stable_hash({
            "agent_version": s.agent_version,
            "environment_version": s.environment_version,
            "max_steps": s.max_steps,
        })

    def _case_fingerprint(self, run, expected_checks, exposure) -> str:
        s = run.scenario
        return stable_hash({
            "scenario_manifest_hash": self._manifest_hash(run),
            "task_contract_hash": s.task_contract_hash,
            "validator_bundle_hash": s.validator_hash,
            "execution_contract_hash": self._execution_contract_hash(run),
            "expected_checks": [x.model_dump(mode="json") for x in expected_checks],
            "exposure_requirement": exposure.model_dump(mode="json"),
        })

    def register_case(self, source_run_id: str, *, name: str, description: str = "") -> RegressionCase:
        run = self.run_service.get(source_run_id)
        if not run or not run.scenario:
            raise RegressionValidationError("source run or scenario unavailable")
        if not run.scenario.fixture_snapshot:
            raise RegressionValidationError("reconstructible fixture snapshot unavailable")
        if stable_hash({"files": run.scenario.fixture_snapshot, "resource_ids": {"sales.csv": "sales_input"}}) != run.scenario.fixture_hash:
            raise RegressionValidationError("fixture snapshot/hash mismatch")

        expected = [ExpectedCheck(check_id=v.name, required=True) for v in run.validations]
        p_hash = stable_hash({
            "kind": run.scenario.perturbation.value,
            "version": run.scenario.perturbation_version,
            "parameters": run.scenario.perturbation_parameters,
        })
        exposure = ExposureRequirement(
            mode="must_activate" if run.scenario.perturbation == PerturbationKind.FILE_MOVED else "none",
            perturbation_spec_hash=p_hash if run.scenario.perturbation == PerturbationKind.FILE_MOVED else None,
        )
        fingerprint = self._case_fingerprint(run, expected, exposure)
        if fingerprint in self.case_fingerprints:
            cid, ver = self.case_fingerprints[fingerprint]
            return self.case_versions[(cid, ver)]

        manifest_hash = self._manifest_hash(run)
        case_id = str(uuid4())
        content = {
            "scenario_manifest_hash": manifest_hash,
            "initial_fixture_hash": run.scenario.fixture_hash,
            "task_contract_hash": run.scenario.task_contract_hash,
            "validator_bundle_hash": run.scenario.validator_hash,
            "execution_contract_hash": self._execution_contract_hash(run),
            "expected_checks": [x.model_dump(mode="json") for x in expected],
            "exposure_requirement": exposure.model_dump(mode="json"),
        }
        case = RegressionCase(
            case_id=case_id,
            name=name,
            description=description,
            source_run_id=run.run_id,
            scenario_manifest_id=f"scenario:{manifest_hash[:16]}",
            scenario_manifest_hash=manifest_hash,
            initial_fixture_ref=f"run:{run.run_id}:fixture",
            initial_fixture_hash=run.scenario.fixture_hash,
            task_contract_hash=run.scenario.task_contract_hash,
            validator_bundle_hash=run.scenario.validator_hash,
            execution_contract_hash=self._execution_contract_hash(run),
            expected_checks=expected,
            exposure_requirement=exposure,
            case_content_hash=stable_hash(content),
        )
        self.cases[case.case_id] = case
        self.case_versions[(case.case_id, case.case_version)] = case
        self.case_fingerprints[fingerprint] = (case.case_id, case.case_version)
        return case

    def list_cases(self) -> list[RegressionCase]:
        return list(self.cases.values())

    def get_case(self, case_id: str, version: int = 1) -> RegressionCase | None:
        return self.case_versions.get((case_id, version))

    def _build_suite(self, repair_id: str, current_case_id: str, clean_case_id: str, historical_case_ids: list[str]):
        proposal = self.repair_service.repairs.get(repair_id)
        if not proposal:
            raise KeyError(repair_id)
        errors = self.repair_service.validate_proposal(proposal)
        if errors or proposal.status != "valid":
            raise RepairValidationError("invalid repair proposal: " + "; ".join(errors or proposal.validation_errors))
        base = self.run_service.policies[proposal.base_policy_id]
        candidate = self.repair_service._candidate_policy(proposal)

        roles = [(current_case_id, "current", True), (clean_case_id, "clean", True)]
        roles.extend((cid, "historical", False) for cid in historical_case_ids)
        members: list[SuiteMember] = []
        seen: set[tuple[str, int]] = set()
        role_priority = {"historical": 0, "clean": 1, "current": 2}
        by_identity: dict[tuple[str, int], SuiteMember] = {}
        for cid, role, must_pass in roles:
            case = self.get_case(cid)
            if not case:
                raise RegressionValidationError(f"regression case not found: {cid}")
            ident = (case.case_id, case.case_version)
            member = SuiteMember(
                case_id=case.case_id,
                case_version=case.case_version,
                case_content_hash=case.case_content_hash,
                role=role,
                must_pass_candidate=must_pass,
            )
            old = by_identity.get(ident)
            if old is None or role_priority[role] > role_priority[old.role]:
                by_identity[ident] = member
        members = list(by_identity.values())
        if sum(m.role == "current" for m in members) != 1:
            raise RegressionValidationError("suite must contain exactly one current case")
        if sum(m.role == "clean" for m in members) < 1:
            raise RegressionValidationError("suite must contain at least one clean case")
        suite_payload = {
            "members": [m.model_dump(mode="json") for m in members],
            "base_policy_hash": base.policy_hash,
            "candidate_policy_hash": candidate.policy_hash,
            "gate_version": "deterministic-regression-v1",
        }
        suite = EvaluationSuiteSnapshot(
            members=members,
            base_policy_id=base.policy_id,
            base_policy_hash=base.policy_hash,
            candidate_policy_id=candidate.policy_id,
            candidate_policy_hash=candidate.policy_hash,
            suite_hash=stable_hash(suite_payload),
        )
        self.suites[suite.suite_id] = suite
        return proposal, base, candidate, suite

    @staticmethod
    def _required_pass(run, case: RegressionCase) -> bool:
        results = {v.name: v.passed for v in run.validations}
        return all(results.get(ch.check_id) is True for ch in case.expected_checks if ch.required)

    @staticmethod
    def _provider_harness_failure(run) -> bool:
        meta = run.model_execution or {}
        for role in ("agent", "diagnostic"):
            part = meta.get(role) or {}
            if part.get("failure_kind") == "provider_harness":
                return True
        return False

    def _pair_result(self, member, case, baseline, candidate, comparison) -> CasePairResult:
        reasons: list[str] = []
        valid = comparison.comparable and baseline.trace_complete and candidate.trace_complete
        if self._provider_harness_failure(baseline) or self._provider_harness_failure(candidate):
            valid = False
            reasons.append("provider/harness failure is not a task regression")
        if not comparison.comparable:
            reasons.append("scenario invariant mismatch")
        if not baseline.trace_complete or not candidate.trace_complete:
            reasons.append("incomplete run")
        if case.exposure_requirement.mode == "must_activate":
            if not comparison.baseline_activation.activated or not comparison.candidate_activation.activated:
                valid = False
                reasons.append("required perturbation not activated in both arms")
            elif not comparison.baseline_activation.exposure_verified or not comparison.candidate_activation.exposure_verified:
                valid = False
                reasons.append("required perturbation exposure not verified in both arms")
        if not valid:
            bo = co = CaseOutcome.INVALID
            trans = PairTransition.INVALID
        else:
            bo = CaseOutcome.PASS if self._required_pass(baseline, case) else CaseOutcome.FAIL
            co = CaseOutcome.PASS if self._required_pass(candidate, case) else CaseOutcome.FAIL
            trans = {
                (CaseOutcome.PASS, CaseOutcome.PASS): PairTransition.PRESERVED,
                (CaseOutcome.FAIL, CaseOutcome.PASS): PairTransition.IMPROVED,
                (CaseOutcome.PASS, CaseOutcome.FAIL): PairTransition.REGRESSION,
                (CaseOutcome.FAIL, CaseOutcome.FAIL): PairTransition.UNRESOLVED,
            }[(bo, co)]
        return CasePairResult(
            case_id=case.case_id,
            case_version=case.case_version,
            role=member.role,
            baseline_run_id=baseline.run_id,
            candidate_run_id=candidate.run_id,
            baseline_outcome=bo,
            candidate_outcome=co,
            transition=trans,
            comparison=comparison,
            valid_pair=valid,
            reasons=reasons,
        )

    @staticmethod
    def _metrics(results: list[CasePairResult]) -> ReliabilityMetrics:
        n = len(results)
        valid = [r for r in results if r.valid_pair]
        bp = sum(r.baseline_outcome == CaseOutcome.PASS for r in valid)
        cp = sum(r.candidate_outcome == CaseOutcome.PASS for r in valid)
        improvements = sum(r.transition == PairTransition.IMPROVED for r in valid)
        regressions = sum(r.transition == PairTransition.REGRESSION for r in valid)
        unresolved = sum(r.transition == PairTransition.UNRESOLVED for r in valid)
        preserved = sum(r.transition == PairTransition.PRESERVED for r in valid)
        p = len(valid)
        return ReliabilityMetrics(
            scheduled_cases=n,
            baseline_valid=p,
            candidate_valid=p,
            valid_pairs=p,
            baseline_passes=bp,
            candidate_passes=cp,
            improvement_count=improvements,
            regression_count=regressions,
            unresolved_failure_count=unresolved,
            preserved_count=preserved,
            baseline_coverage=(p / n) if n else None,
            candidate_coverage=(p / n) if n else None,
            baseline_case_success_rate=(bp / p) if p else None,
            candidate_case_success_rate=(cp / p) if p else None,
            paired_net_change=((improvements - regressions) / p) if p else None,
            invalid_or_incomplete_pairs=n - p,
        )

    @staticmethod
    def _gate(results: list[CasePairResult], members: list[SuiteMember]):
        reasons: list[str] = []
        by_id = {(r.case_id, r.case_version): r for r in results}
        member_by = {(m.case_id, m.case_version): m for m in members}
        complete = all(r.valid_pair for r in results)

        # An established valid veto is sufficient for REJECT even with incomplete coverage.
        for r in results:
            if r.valid_pair and r.transition == PairTransition.REGRESSION:
                reasons.append(f"regression established in case {r.case_id}")
            m = member_by[(r.case_id, r.case_version)]
            if r.valid_pair and m.must_pass_candidate and r.candidate_outcome != CaseOutcome.PASS:
                reasons.append(f"candidate failed required-pass case {r.case_id}")
        if reasons:
            return GateDecision.REJECT, reasons, complete

        current = next(r for r in results if r.role == "current")
        clean = [r for r in results if r.role == "clean"]
        if not current.valid_pair or any(not r.valid_pair for r in clean):
            return GateDecision.INCONCLUSIVE, ["required current/clean comparison invalid or incomplete"], False
        if current.baseline_outcome != CaseOutcome.FAIL:
            return GateDecision.INCONCLUSIVE, ["current baseline did not reproduce expected failure"], complete
        if current.candidate_outcome != CaseOutcome.PASS:
            return GateDecision.REJECT, ["current candidate did not resolve required case"], complete
        if any(r.baseline_outcome != CaseOutcome.PASS for r in clean):
            return GateDecision.INCONCLUSIVE, ["clean baseline failed"], complete
        if any(r.candidate_outcome != CaseOutcome.PASS for r in clean):
            return GateDecision.REJECT, ["clean candidate regressed"], complete
        if not complete:
            return GateDecision.INCONCLUSIVE, ["suite contains invalid or incomplete historical comparison"], False
        return GateDecision.ACCEPT, ["passed Mission 04 deterministic regression gate"], True

    def evaluate_suite(
        self,
        repair_id: str,
        *,
        current_case_id: str,
        clean_case_id: str,
        historical_case_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> EvaluationRecord:
        historical_case_ids = historical_case_ids or []
        key = f"m04:{idempotency_key}" if idempotency_key else None
        if key and key in self.idempotency:
            return self.repair_service.evaluations[self.idempotency[key]]

        proposal, base, candidate, suite = self._build_suite(
            repair_id, current_case_id, clean_case_id, historical_case_ids
        )
        evaluation_id = str(uuid4())
        results: list[CasePairResult] = []
        for member in suite.members:
            case = self.get_case(member.case_id, member.case_version)
            source = self.run_service.get(case.source_run_id)
            if not source or not source.scenario:
                raise RegressionValidationError("case source scenario unavailable")
            # v1 runner can reconstruct only its canonical immutable base fixture.
            if source.scenario.fixture_hash != case.initial_fixture_hash:
                raise RegressionValidationError("case fixture hash drift")
            perturbation = source.scenario.perturbation
            baseline = self.run_service.execute(
                perturbation,
                policy=base,
                replay_of_run_id=case.source_run_id,
                evaluation_id=evaluation_id,
                evaluation_case=member.role,
                evaluation_arm="baseline",
                regression_case_id=case.case_id,
                regression_case_version=case.case_version,
            )
            candidate_run = self.run_service.execute(
                perturbation,
                policy=candidate,
                replay_of_run_id=case.source_run_id,
                evaluation_id=evaluation_id,
                evaluation_case=member.role,
                evaluation_arm="candidate",
                regression_case_id=case.case_id,
                regression_case_version=case.case_version,
            )
            cmp = self.repair_service.compare(baseline, candidate_run)
            # Verify executable semantics against immutable case content.
            if baseline.scenario.task_contract_hash != case.task_contract_hash:
                cmp = cmp.model_copy(update={
                    "comparable": False,
                    "noncomparability_reasons": cmp.noncomparability_reasons + ["task contract drift"],
                })
            if baseline.scenario.validator_hash != case.validator_bundle_hash:
                cmp = cmp.model_copy(update={
                    "comparable": False,
                    "noncomparability_reasons": cmp.noncomparability_reasons + ["validator drift"],
                })
            results.append(self._pair_result(member, case, baseline, candidate_run, cmp))

        decision, reasons, complete = self._gate(results, suite.members)
        metrics = self._metrics(results)
        current = next(r for r in results if r.role == "current")
        clean = next(r for r in results if r.role == "clean")
        statement = (
            "The bounded re-observation policy improved the current deterministic failure while preserving all "
            "valid required cases in the pinned Mission 04 suite."
            if decision == GateDecision.ACCEPT else
            "Mission 04 did not establish suite-wide acceptance; see case-level transitions and gate reasons."
        )
        record = EvaluationRecord(
            evaluation_id=evaluation_id,
            repair_id=repair_id,
            baseline_policy_id=base.policy_id,
            candidate_policy_id=candidate.policy_id,
            moved_baseline_run_id=current.baseline_run_id,
            moved_candidate_run_id=current.candidate_run_id,
            clean_baseline_run_id=clean.baseline_run_id,
            clean_candidate_run_id=clean.candidate_run_id,
            moved_comparison=current.comparison,
            clean_comparison=clean.comparison,
            decision=decision,
            reasons=reasons,
            causal_statement=statement,
            suite_snapshot=suite,
            case_results=results,
            metrics=metrics,
            evaluation_complete=complete,
        )
        self.repair_service.evaluations[evaluation_id] = record
        if key:
            self.idempotency[key] = evaluation_id
        return record
