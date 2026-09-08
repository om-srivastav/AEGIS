import asyncio
from copy import deepcopy

import pytest

from app.envs.virtual_files import VirtualFileEnvironment
from app.models.schemas import (
    AgentPolicy,
    EventType,
    GateDecision,
    PerturbationKind,
    RepairProposal,
    RunStatus,
)
from app.services.diagnosis import DiagnosisService
from app.services.policy import ResourceRecoveryController, compute_policy_hash, make_policy
from app.services.repair import RepairService, RepairValidationError
from app.services.runner import RunService


def _prepared():
    runs = RunService()
    source = runs.execute(PerturbationKind.FILE_MOVED)
    diagnoses = DiagnosisService(runs)
    diagnosis = asyncio.run(diagnoses.diagnose(source.run_id))
    repairs = RepairService(runs, diagnoses)
    proposal = repairs.propose(diagnosis.diagnosis_id)
    return runs, source, diagnoses, diagnosis, repairs, proposal


def test_valid_repair_proposal_only_enables_reobservation():
    _, _, _, _, repairs, proposal = _prepared()
    assert proposal.status == "valid"
    assert proposal.old_configuration.enabled is False
    assert proposal.new_configuration.enabled is True
    assert repairs.validate_proposal(proposal) == []


def test_proposal_rejects_any_change_besides_enabled():
    _, _, _, _, repairs, proposal = _prepared()
    bad_cfg = proposal.new_configuration.model_copy(update={"on_no_match": "fail"})
    # Force a real second behavioral diff using model construction since literals block invalid values.
    bad = proposal.model_copy(update={
        "new_configuration": proposal.new_configuration.model_copy(update={"max_discovery_calls_per_run": 2})
    })
    assert any("only enabled" in e for e in repairs.validate_proposal(bad))


def test_base_hash_mismatch_rejected():
    _, _, _, _, repairs, proposal = _prepared()
    bad = proposal.model_copy(update={"base_policy_hash": "0" * 64})
    assert any("base policy hash" in e for e in repairs.validate_proposal(bad))


def test_moved_candidate_recovers_by_exact_resource_identity():
    runs = RunService()
    baseline = runs.execute(PerturbationKind.FILE_MOVED)
    candidate_policy = make_policy(enabled=True, parent_policy_id=baseline.effective_policy_id, version=2)
    candidate = runs.execute(PerturbationKind.FILE_MOVED, policy=candidate_policy)
    assert baseline.status == RunStatus.FAILED
    assert candidate.status == RunStatus.PASSED
    assert sum(1 for e in candidate.events if e.kind == EventType.TOOL_CALL and e.message == "list_files") == 1
    assert any(e.kind == EventType.RECOVERY and e.message == "Recovery succeeded" for e in candidate.events)


def test_clean_candidate_passes_without_recovery():
    runs = RunService()
    candidate = runs.execute(PerturbationKind.NONE, policy=make_policy(enabled=True, version=2))
    assert candidate.status == RunStatus.PASSED
    assert not any(e.kind == EventType.RECOVERY for e in candidate.events)


def _emit_collector():
    events = []
    def emit(kind, message, payload):
        events.append((kind, message, payload))
    return events, emit


def test_recovery_fails_safely_on_zero_identity_matches():
    policy = make_policy(enabled=True)
    from app.models.schemas import TaskSpec
    task = TaskSpec()
    events, emit = _emit_collector()
    env = VirtualFileEnvironment(perturbation=PerturbationKind.NONE)
    env.files = {"archive/other.csv": "x"}
    env.resource_ids = {"archive/other.csv": "other_resource"}
    controller = ResourceRecoveryController(policy, task, emit)
    with pytest.raises(FileNotFoundError):
        controller.recover_missing_resource(env, "sales.csv")
    assert controller.recovery_read_calls == 0


def test_recovery_fails_safely_on_ambiguous_identity_matches():
    policy = make_policy(enabled=True)
    from app.models.schemas import TaskSpec
    task = TaskSpec()
    events, emit = _emit_collector()
    env = VirtualFileEnvironment(perturbation=PerturbationKind.NONE)
    env.files = {"a.csv": "x", "b.csv": "x"}
    env.resource_ids = {"a.csv": "sales_input", "b.csv": "sales_input"}
    controller = ResourceRecoveryController(policy, task, emit)
    with pytest.raises(FileNotFoundError):
        controller.recover_missing_resource(env, "sales.csv")
    assert controller.recovery_read_calls == 0
    assert any(payload.get("reason") == "ambiguous" for _, _, payload in events)


def test_recovery_fails_on_incomplete_listing():
    policy = make_policy(enabled=True)
    from app.models.schemas import TaskSpec
    task = TaskSpec()
    _, emit = _emit_collector()
    env = VirtualFileEnvironment(perturbation=PerturbationKind.NONE)
    env.listing_complete = False
    controller = ResourceRecoveryController(policy, task, emit)
    with pytest.raises(FileNotFoundError):
        controller.recover_missing_resource(env, "sales.csv")
    assert controller.recovery_read_calls == 0


def test_non_file_not_found_does_not_trigger_recovery():
    runs = RunService()
    candidate = runs.execute(PerturbationKind.TOOL_TIMEOUT, policy=make_policy(enabled=True, version=2))
    assert candidate.status == RunStatus.FAILED
    assert not any(e.kind == EventType.RECOVERY for e in candidate.events)


def test_fresh_runs_have_distinct_ids_and_same_fixture_snapshot():
    runs = RunService()
    a = runs.execute(PerturbationKind.FILE_MOVED)
    b = runs.execute(PerturbationKind.FILE_MOVED)
    assert a.run_id != b.run_id
    assert a.scenario.fixture_hash == b.scenario.fixture_hash
    assert a.scenario.fixture_snapshot == b.scenario.fixture_snapshot
    assert "report.json" not in a.scenario.fixture_snapshot
    assert "report.json" not in b.scenario.fixture_snapshot


def test_real_four_run_evaluation_accepts():
    runs, source, _, diagnosis, repairs, proposal = _prepared()
    evaluation = repairs.evaluate(proposal.repair_id)
    assert evaluation.decision == GateDecision.ACCEPT
    mb = runs.get(evaluation.moved_baseline_run_id)
    mc = runs.get(evaluation.moved_candidate_run_id)
    cb = runs.get(evaluation.clean_baseline_run_id)
    cc = runs.get(evaluation.clean_candidate_run_id)
    assert mb.status == RunStatus.FAILED
    assert mc.status == RunStatus.PASSED
    assert cb.status == RunStatus.PASSED
    assert cc.status == RunStatus.PASSED
    assert evaluation.moved_comparison.baseline_activation.exposure_verified
    assert evaluation.moved_comparison.candidate_activation.exposure_verified
    assert not evaluation.clean_comparison.recovery_summary.attempted
    assert source.run_id == proposal.source_run_id


def test_idempotency_does_not_execute_duplicate_evaluation():
    runs, _, _, _, repairs, proposal = _prepared()
    before = len(runs.runs)
    a = repairs.evaluate(proposal.repair_id, idempotency_key="same-key")
    after_first = len(runs.runs)
    b = repairs.evaluate(proposal.repair_id, idempotency_key="same-key")
    assert a.evaluation_id == b.evaluation_id
    assert len(runs.runs) == after_first
    assert after_first == before + 4


def test_comparison_invariant_mismatch_is_not_comparable():
    runs, _, _, _, repairs, proposal = _prepared()
    base = runs.policies[proposal.base_policy_id]
    cand = make_policy(enabled=True, parent_policy_id=base.policy_id, version=2)
    b = runs.execute(PerturbationKind.FILE_MOVED, policy=base)
    c = runs.execute(PerturbationKind.FILE_MOVED, policy=cand)
    c.scenario.task_contract_hash = "different"
    cmp = repairs.compare(b, c)
    assert cmp.comparable is False
    assert cmp.original_failure_resolved is None


def test_source_run_and_policy_are_not_mutated_by_evaluation():
    runs, source, _, _, repairs, proposal = _prepared()
    source_snapshot = source.model_dump(mode="json")
    base_snapshot = runs.policies[proposal.base_policy_id].model_dump(mode="json")
    repairs.evaluate(proposal.repair_id)
    assert source.model_dump(mode="json") == source_snapshot
    assert runs.policies[proposal.base_policy_id].model_dump(mode="json") == base_snapshot
