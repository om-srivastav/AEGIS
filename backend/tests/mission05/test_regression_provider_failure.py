from app.models.schemas import PairTransition, PerturbationKind
from app.services.diagnosis import DiagnosisService
from app.services.regression import RegressionVaultService
from app.services.repair import RepairService
from app.services.runner import RunService


def test_provider_harness_failure_cannot_be_counted_as_regression():
    service = RunService()
    diagnosis = DiagnosisService(service)
    repair = RepairService(service, diagnosis)
    vault = RegressionVaultService(service, repair)
    baseline = service.execute(PerturbationKind.NONE)
    candidate = service.execute(PerturbationKind.NONE)
    candidate.model_execution = {
        "schema_version": "1",
        "agent": {"failure_kind": "provider_harness", "failure_code": "provider_http_503"},
    }
    case = vault.register_case(baseline.run_id, name="clean")
    comparison = repair.compare(baseline, candidate)
    member = type("M", (), {"role":"historical"})()
    result = vault._pair_result(member, case, baseline, candidate, comparison)
    assert result.valid_pair is False
    assert result.transition == PairTransition.INVALID
    assert any("provider/harness" in reason for reason in result.reasons)
