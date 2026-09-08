import asyncio

from app.models.schemas import EventType, PerturbationKind, RunStatus
from app.providers.model_contracts import Limits
from app.services.runner import RunService

from .fakes import ScriptedTransport, tool


CORRECT = '{"order_count":3,"total_revenue":600.0}'


def test_model_agent_clean_run_uses_real_sandbox_and_validator():
    service = RunService()
    transport = ScriptedTransport([
        tool("c1", "read_file", {"path": "sales.csv"}),
        tool("c2", "write_report", {"content": CORRECT}),
    ])
    run = asyncio.run(service.execute_model(
        PerturbationKind.NONE,
        transport=transport,
        model="offline-fixture-model",
        limits=Limits(),
    ))
    assert run.status == RunStatus.PASSED
    assert run.model_execution["agent"]["execution_status"] == "completed"
    assert run.model_execution["agent"]["resolved_models"] == ["offline-fixture-model"]
    assert any(e.kind == EventType.MODEL and e.message == "agent.tool.proposed" for e in run.events)
    assert any(e.kind == EventType.TOOL_CALL and e.message == "write_report" for e in run.events)


def test_model_agent_moved_resource_observes_error_then_recovers():
    service = RunService()
    transport = ScriptedTransport([
        tool("c1", "read_file", {"path": "sales.csv"}),
        tool("c2", "list_files", {}),
        tool("c3", "read_file", {"path": "archive/sales.csv"}),
        tool("c4", "write_report", {"content": CORRECT}),
    ])
    run = asyncio.run(service.execute_model(
        PerturbationKind.FILE_MOVED,
        transport=transport,
        model="offline-fixture-model",
        limits=Limits(),
    ))
    assert run.status == RunStatus.PASSED
    assert any(e.kind == EventType.PERTURBATION for e in run.events)
    assert any(
        e.kind == EventType.TOOL_RESULT and e.payload.get("error_code") == "not_found"
        for e in run.events
    )
    assert any(e.kind == EventType.TOOL_CALL and e.message == "list_files" for e in run.events)


def test_successful_execution_with_wrong_report_still_fails_validator():
    service = RunService()
    transport = ScriptedTransport([
        tool("c1", "write_report", {"content": '{"order_count":99,"total_revenue":1}'}),
    ])
    run = asyncio.run(service.execute_model(
        PerturbationKind.NONE,
        transport=transport,
        model="offline-fixture-model",
        limits=Limits(),
    ))
    assert run.model_execution["agent"]["execution_status"] == "completed"
    assert run.status == RunStatus.FAILED
    assert next(v for v in run.validations if v.name == "report_values").passed is False
