from __future__ import annotations
import csv
import io

from app.envs.virtual_files import VirtualFileEnvironment
from app.models.schemas import EventType, TaskSpec
from app.services.policy import ResourceRecoveryController


class ScriptedSalesAgent:
    """Deterministic Agent Under Test used to validate AEGIS mechanics."""

    name = "scripted-sales-agent-v1"

    def run(self, env: VirtualFileEnvironment, emit, policy, task: TaskSpec) -> None:
        recovery = ResourceRecoveryController(policy, task, emit)
        emit(EventType.OBSERVATION, "Need sales data", {"target": task.resource.original_path})
        emit(EventType.DECISION, "Read sales.csv directly", {})
        emit(EventType.TOOL_CALL, "read_file", {"path": task.resource.original_path})
        try:
            raw = env.read_file(task.resource.original_path)
            emit(EventType.TOOL_RESULT, "sales.csv returned", {"bytes": len(raw)})
        except FileNotFoundError as exc:
            emit(EventType.TOOL_RESULT, "read_file failed", {
                "path": task.resource.original_path,
                "error_code": "FILE_NOT_FOUND",
                "error_type": type(exc).__name__,
            })
            raw = recovery.recover_missing_resource(env, task.resource.original_path)

        rows = list(csv.DictReader(io.StringIO(raw)))
        total_revenue = 0.0
        for row in rows:
            total_revenue += int(row["quantity"]) * float(row["price"])

        report = {"order_count": len(rows), "total_revenue": total_revenue}
        emit(EventType.DECISION, "Write computed report", report)
        emit(EventType.TOOL_CALL, "write_json", {"path": "report.json"})
        env.write_json("report.json", report)
        emit(EventType.TOOL_RESULT, "report.json written", {})
