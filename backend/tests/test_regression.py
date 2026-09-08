import asyncio

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import (
    CaseOutcome,
    GateDecision,
    PairTransition,
    PerturbationKind,
)
from app.services.diagnosis import DiagnosisService
from app.services.regression import RegressionVaultService
from app.services.repair import RepairService
from app.services.runner import RunService


def _prepared():
    runs = RunService()
    source = runs.execute(PerturbationKind.FILE_MOVED)
    diagnoses = DiagnosisService(runs)
    diagnosis = asyncio.run(diagnoses.diagnose(source.run_id))
    repairs = RepairService(runs, diagnoses)
    proposal = repairs.propose(diagnosis.diagnosis_id)
    vault = RegressionVaultService(runs, repairs)
    return runs, source, repairs, proposal, vault


def test_register_case_from_executed_run_and_deduplicate():
    runs, source, repairs, proposal, vault = _prepared()
    a = vault.register_case(source.run_id, name="moved failure")
    b = vault.register_case(source.run_id, name="duplicate discovery")
    assert a.case_id == b.case_id
    assert a.case_content_hash == b.case_content_hash
    assert a.initial_fixture_hash == source.scenario.fixture_hash
    assert a.expected_checks


def test_case_pins_scenario_without_policy_identity():
    runs, source, repairs, proposal, vault = _prepared()
    case = vault.register_case(source.run_id, name="moved failure")
    # Same deterministic scenario under a different policy must still represent the same case semantics.
    candidate_policy = repairs._candidate_policy(proposal)
    candidate_run = runs.execute(PerturbationKind.FILE_MOVED, policy=candidate_policy)
    assert vault._manifest_hash(source) == vault._manifest_hash(candidate_run)
    assert case.scenario_manifest_hash == vault._manifest_hash(candidate_run)


def test_actual_three_case_suite_accepts_with_unresolved_historical_failure():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    historical_source = runs.execute(PerturbationKind.FIELD_RENAMED)
    current = vault.register_case(source.run_id, name="current moved-resource failure")
    clean = vault.register_case(clean_source.run_id, name="clean contract")
    historical = vault.register_case(historical_source.run_id, name="historical schema-drift failure")

    before = len(runs.runs)
    evaluation = vault.evaluate_suite(
        proposal.repair_id,
        current_case_id=current.case_id,
        clean_case_id=clean.case_id,
        historical_case_ids=[historical.case_id],
    )
    assert len(runs.runs) == before + 6
    assert evaluation.decision == GateDecision.ACCEPT
    assert evaluation.evaluation_complete is True
    transitions = {r.role: r.transition for r in evaluation.case_results}
    assert transitions["current"] == PairTransition.IMPROVED
    assert transitions["clean"] == PairTransition.PRESERVED
    assert transitions["historical"] == PairTransition.UNRESOLVED
    assert evaluation.metrics.scheduled_cases == 3
    assert evaluation.metrics.valid_pairs == 3
    assert evaluation.metrics.improvement_count == 1
    assert evaluation.metrics.regression_count == 0
    assert evaluation.metrics.unresolved_failure_count == 1
    assert evaluation.metrics.preserved_count == 1
    assert evaluation.metrics.baseline_passes == 1
    assert evaluation.metrics.candidate_passes == 2
    assert evaluation.metrics.paired_net_change == 1 / 3


def test_suite_members_are_frozen_after_vault_catalog_change():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    current = vault.register_case(source.run_id, name="current")
    clean = vault.register_case(clean_source.run_id, name="clean")
    evaluation = vault.evaluate_suite(
        proposal.repair_id, current_case_id=current.case_id, clean_case_id=clean.case_id
    )
    snapshot = evaluation.suite_snapshot.model_dump(mode="json")
    # Mutable catalog metadata may change, but finalized suite content remains its own snapshot.
    vault.cases[clean.case_id] = clean.model_copy(update={"active": False})
    assert evaluation.suite_snapshot.model_dump(mode="json") == snapshot


def test_duplicate_case_in_history_is_not_double_counted():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    current = vault.register_case(source.run_id, name="current")
    clean = vault.register_case(clean_source.run_id, name="clean")
    evaluation = vault.evaluate_suite(
        proposal.repair_id,
        current_case_id=current.case_id,
        clean_case_id=clean.case_id,
        historical_case_ids=[current.case_id, clean.case_id],
    )
    assert evaluation.metrics.scheduled_cases == 2
    assert len(evaluation.suite_snapshot.members) == 2


def test_established_pass_to_fail_is_a_regression_and_rejects():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    hist_source = runs.execute(PerturbationKind.FIELD_RENAMED)
    current = vault.register_case(source.run_id, name="current")
    clean = vault.register_case(clean_source.run_id, name="clean")
    hist = vault.register_case(hist_source.run_id, name="history")
    evaluation = vault.evaluate_suite(
        proposal.repair_id,
        current_case_id=current.case_id,
        clean_case_id=clean.case_id,
        historical_case_ids=[hist.case_id],
    )
    hist_result = next(r for r in evaluation.case_results if r.role == "historical")
    synthetic_regression = hist_result.model_copy(update={
        "baseline_outcome": CaseOutcome.PASS,
        "candidate_outcome": CaseOutcome.FAIL,
        "transition": PairTransition.REGRESSION,
        "valid_pair": True,
    })
    synthetic_results = [
        synthetic_regression if r.case_id == hist_result.case_id else r
        for r in evaluation.case_results
    ]
    decision, reasons, complete = vault._gate(synthetic_results, evaluation.suite_snapshot.members)
    assert decision == GateDecision.REJECT
    assert any("regression established" in x for x in reasons)


def test_fail_to_fail_is_unresolved_not_regression():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    hist_source = runs.execute(PerturbationKind.FIELD_RENAMED)
    current = vault.register_case(source.run_id, name="current")
    clean = vault.register_case(clean_source.run_id, name="clean")
    hist = vault.register_case(hist_source.run_id, name="history")
    evaluation = vault.evaluate_suite(
        proposal.repair_id,
        current_case_id=current.case_id,
        clean_case_id=clean.case_id,
        historical_case_ids=[hist.case_id],
    )
    hist_result = next(r for r in evaluation.case_results if r.role == "historical")
    assert hist_result.baseline_outcome == CaseOutcome.FAIL
    assert hist_result.candidate_outcome == CaseOutcome.FAIL
    assert hist_result.transition == PairTransition.UNRESOLVED
    assert evaluation.metrics.regression_count == 0


def test_suite_evaluation_idempotency_does_not_add_runs():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    current = vault.register_case(source.run_id, name="current")
    clean = vault.register_case(clean_source.run_id, name="clean")
    a = vault.evaluate_suite(
        proposal.repair_id,
        current_case_id=current.case_id,
        clean_case_id=clean.case_id,
        idempotency_key="suite-key",
    )
    after = len(runs.runs)
    b = vault.evaluate_suite(
        proposal.repair_id,
        current_case_id=current.case_id,
        clean_case_id=clean.case_id,
        idempotency_key="suite-key",
    )
    assert a.evaluation_id == b.evaluation_id
    assert len(runs.runs) == after


def test_metrics_zero_valid_pairs_are_safe():
    runs, source, repairs, proposal, vault = _prepared()
    clean_source = runs.execute(PerturbationKind.NONE)
    current = vault.register_case(source.run_id, name="current")
    clean = vault.register_case(clean_source.run_id, name="clean")
    evaluation = vault.evaluate_suite(
        proposal.repair_id, current_case_id=current.case_id, clean_case_id=clean.case_id
    )
    invalid = [
        r.model_copy(update={
            "valid_pair": False,
            "baseline_outcome": CaseOutcome.INVALID,
            "candidate_outcome": CaseOutcome.INVALID,
            "transition": PairTransition.INVALID,
        })
        for r in evaluation.case_results
    ]
    metrics = vault._metrics(invalid)
    assert metrics.valid_pairs == 0
    assert metrics.baseline_case_success_rate is None
    assert metrics.candidate_case_success_rate is None
    assert metrics.paired_net_change is None


def test_regression_case_api_and_suite_api_round_trip():
    # Use app-global services; create source/current clean runs through the API.
    client = TestClient(app)
    moved = client.post("/api/runs", json={"perturbation": "file_moved"}).json()
    clean = client.post("/api/runs", json={"perturbation": "none"}).json()
    diagnosis = client.post(f"/api/v1/runs/{moved['run_id']}/diagnoses").json()
    repair = client.post(f"/api/v1/diagnoses/{diagnosis['diagnosis_id']}/repairs").json()
    current_case = client.post("/api/v1/regression-cases", json={
        "source_run_id": moved["run_id"], "name": "api current"
    })
    clean_case = client.post("/api/v1/regression-cases", json={
        "source_run_id": clean["run_id"], "name": "api clean"
    })
    assert current_case.status_code == 200
    assert clean_case.status_code == 200
    response = client.post(
        f"/api/v1/repairs/{repair['repair_id']}/suite-evaluations",
        json={
            "current_case_id": current_case.json()["case_id"],
            "clean_case_id": clean_case.json()["case_id"],
            "historical_case_ids": [],
        },
        headers={"Idempotency-Key": "api-m04"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ACCEPT"
    assert body["metrics"]["scheduled_cases"] == 2
