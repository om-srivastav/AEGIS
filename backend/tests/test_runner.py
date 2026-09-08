from app.models.schemas import PerturbationKind, RunStatus
from app.services.runner import RunService


def test_baseline_passes():
    service = RunService()
    run = service.execute(PerturbationKind.NONE)
    assert run.status == RunStatus.PASSED
    assert run.score == 1.0


def test_file_moved_exposes_failure():
    service = RunService()
    run = service.execute(PerturbationKind.FILE_MOVED)
    assert run.status == RunStatus.FAILED
    assert any(e.payload.get("error_type") == "FileNotFoundError" for e in run.events)


def test_tool_timeout_exposes_recovery_failure():
    service = RunService()
    run = service.execute(PerturbationKind.TOOL_TIMEOUT)
    assert run.status == RunStatus.FAILED
    assert any(e.payload.get("error_type") == "ToolTimeoutError" for e in run.events)
