from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from app.agents.adapter import VisibleTask
from app.agents.model_agent import AGENT_PROMPT_VERSION, ModelAgent
from app.agents.model_tools import VirtualFileToolPort
from app.agents.scripted_sales_agent import ScriptedSalesAgent
from app.envs.virtual_files import BASE_FILES, BASE_RESOURCE_IDS, VirtualFileEnvironment
from app.models.schemas import (
    AgentPolicy,
    EventType,
    PerturbationKind,
    RunRecord,
    RunStatus,
    ScenarioManifest,
    TraceEvent,
    ValidationResult,
)
from app.providers.model_contracts import Audit, Limits, ModelTransport
from app.services.policy import make_policy


EXPECTED_REPORT = {"order_count": 3, "total_revenue": 600.0}
BASELINE_POLICY = make_policy(enabled=False, version=1)


def _stable_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


class RunService:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.policies: dict[str, AgentPolicy] = {BASELINE_POLICY.policy_id: BASELINE_POLICY}

    def register_policy(self, policy: AgentPolicy) -> None:
        self.policies[policy.policy_id] = policy

    @staticmethod
    def _actor(event_type: EventType) -> str:
        if event_type == EventType.MODEL:
            return "model"
        if event_type in {
            EventType.OBSERVATION,
            EventType.DECISION,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
        }:
            return "agent"
        return "aegis"

    def _trace_hash(self, record: RunRecord) -> str:
        return _stable_hash([e.model_dump(mode="json") for e in record.events])

    def append_model_event(self, run_id: str, event_name: str, payload: dict[str, Any]) -> None:
        """Append a namespaced model event to the existing run trace.

        Diagnostic events happen after execution finalization. They remain excluded from
        deterministic fact extraction and are never automatically exposed back to models.
        """
        record = self.get(run_id)
        if not record:
            raise KeyError(run_id)
        sequence = max((event.sequence for event in record.events), default=0) + 1
        record.events.append(
            TraceEvent(
                run_id=record.run_id,
                sequence=sequence,
                kind=EventType.MODEL,
                actor="diagnostic_model" if event_name.startswith("diagnosis.") else "model",
                elapsed_ms=0,
                message=event_name,
                payload={"event_name": event_name, **payload},
            )
        )
        record.trace_hash = self._trace_hash(record)

    def make_audit(self, run_id: str, *, secrets: tuple[str, ...] = ()) -> Audit:
        return Audit(lambda event, payload: self.append_model_event(run_id, event, payload), secrets=secrets)

    def execute(
        self,
        perturbation: PerturbationKind,
        *,
        policy: AgentPolicy | None = None,
        replay_of_run_id: str | None = None,
        evaluation_id: str | None = None,
        evaluation_case: str | None = None,
        evaluation_arm: str | None = None,
        regression_case_id: str | None = None,
        regression_case_version: int | None = None,
    ) -> RunRecord:
        policy = policy or BASELINE_POLICY
        self.register_policy(policy)
        record = RunRecord(
            perturbation=perturbation,
            status=RunStatus.RUNNING,
            effective_policy_id=policy.policy_id,
            effective_policy_hash=policy.policy_hash,
            replay_of_run_id=replay_of_run_id,
            evaluation_id=evaluation_id,
            evaluation_case=evaluation_case,
            evaluation_arm=evaluation_arm,
            regression_case_id=regression_case_id,
            regression_case_version=regression_case_version,
        )
        task_hash = _stable_hash(record.task.model_dump(mode="json"))
        fixture_snapshot = dict(BASE_FILES)
        record.scenario = ScenarioManifest(
            task_id=record.task.task_id,
            task_version=record.task.version,
            task_contract_hash=task_hash,
            perturbation=perturbation,
            perturbation_parameters={"resource_id": record.task.resource.resource_id}
            if perturbation == PerturbationKind.FILE_MOVED
            else {},
            fixture_hash=_stable_hash({"files": BASE_FILES, "resource_ids": BASE_RESOURCE_IDS}),
            fixture_snapshot=fixture_snapshot,
            policy_version=f"policy-v{policy.version}",
            policy_hash=policy.policy_hash,
            agent_version=ScriptedSalesAgent.name,
        )
        self.runs[record.run_id] = record
        step = 0
        started = perf_counter()

        def emit(event_type: EventType, message: str, payload: dict[str, Any]) -> None:
            nonlocal step
            step += 1
            record.events.append(
                TraceEvent(
                    run_id=record.run_id,
                    sequence=step,
                    kind=event_type,
                    actor=self._actor(event_type),
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    message=message,
                    payload=payload,
                )
            )

        def perturbation_hook(message: str, payload: dict[str, Any]) -> None:
            emit(EventType.PERTURBATION, message, payload)

        emit(
            EventType.RUN_STARTED,
            "Run started",
            {
                "perturbation": perturbation.value,
                "policy_id": policy.policy_id,
                "policy_hash": policy.policy_hash,
            },
        )
        env = VirtualFileEnvironment(perturbation=perturbation, event_hook=perturbation_hook)
        agent = ScriptedSalesAgent()
        try:
            agent.run(env, emit, policy, record.task)
        except Exception as exc:
            emit(
                EventType.OBSERVATION,
                "Agent execution raised an exception",
                {"error_type": type(exc).__name__, "error": str(exc)},
            )
        return self._finalize(record, env, emit)

    async def execute_model(
        self,
        perturbation: PerturbationKind,
        *,
        transport: ModelTransport,
        model: str,
        limits: Limits | None = None,
        secrets: tuple[str, ...] = (),
    ) -> RunRecord:
        """Execute the bounded real-model Agent Under Test against the existing sandbox."""
        limits = limits or Limits()
        controller_hash = _stable_hash({"model_agent_controller": "v1", "tools": ["list_files", "read_file", "write_report"]})
        record = RunRecord(
            perturbation=perturbation,
            status=RunStatus.RUNNING,
            effective_policy_id=None,
            effective_policy_hash=controller_hash,
        )
        task_hash = _stable_hash(record.task.model_dump(mode="json"))
        record.scenario = ScenarioManifest(
            task_id=record.task.task_id,
            task_version=record.task.version,
            task_contract_hash=task_hash,
            perturbation=perturbation,
            perturbation_parameters={"resource_id": record.task.resource.resource_id}
            if perturbation == PerturbationKind.FILE_MOVED
            else {},
            fixture_hash=_stable_hash({"files": BASE_FILES, "resource_ids": BASE_RESOURCE_IDS}),
            fixture_snapshot=dict(BASE_FILES),
            policy_version="model-agent-controller-v1",
            policy_hash=controller_hash,
            agent_version=ModelAgent.name,
        )
        self.runs[record.run_id] = record
        step = 0
        started = perf_counter()

        def emit(event_type: EventType, message: str, payload: dict[str, Any]) -> None:
            nonlocal step
            step += 1
            record.events.append(
                TraceEvent(
                    run_id=record.run_id,
                    sequence=step,
                    kind=event_type,
                    actor=self._actor(event_type),
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    message=message,
                    payload=payload,
                )
            )

        def perturbation_hook(message: str, payload: dict[str, Any]) -> None:
            emit(EventType.PERTURBATION, message, payload)

        def model_audit(event_name: str, payload: dict[str, Any]) -> None:
            emit(EventType.MODEL, event_name, {"event_name": event_name, **payload})

        audit = Audit(model_audit, secrets=secrets)
        emit(
            EventType.RUN_STARTED,
            "Run started",
            {
                "perturbation": perturbation.value,
                "agent_adapter": "model",
                "provider": transport.provider,
                "requested_model": model,
            },
        )
        env = VirtualFileEnvironment(perturbation=perturbation, event_hook=perturbation_hook)
        port = VirtualFileToolPort(env, record.task, emit)
        agent = ModelAgent(transport, model, limits)
        visible_task = VisibleTask(
            prompt=(
                f"{record.task.instruction}\n"
                "The required report JSON object has exactly these fields: "
                "order_count (integer) and total_revenue (number). "
                "Use write_report to submit the JSON object."
            )
        )
        outcome = await agent.run(visible_task, port, audit)
        resolved_models = []
        for event in record.events:
            if event.kind == EventType.MODEL and event.message == "model.response":
                resolved = event.payload.get("response", {}).get("resolved_model")
                if isinstance(resolved, str) and resolved not in resolved_models:
                    resolved_models.append(resolved)
        record.model_execution = {
            "schema_version": "1",
            "agent": {
                "adapter": ModelAgent.name,
                "provider": transport.provider,
                "requested_model": model,
                "resolved_models": resolved_models,
                "prompt_version": AGENT_PROMPT_VERSION,
                "limits": limits.__dict__,
                "execution_status": outcome.execution_status,
                "failure_kind": outcome.failure.kind if outcome.failure else None,
                "failure_code": outcome.failure.code if outcome.failure else None,
                "accounting": outcome.accounting,
                "report_artifact_id": outcome.report_artifact_id,
            },
        }
        return self._finalize(record, env, emit)

    def _finalize(self, record: RunRecord, env: VirtualFileEnvironment, emit) -> RunRecord:
        validations = self._validate(record.run_id, env)
        record.validations = validations
        for result in validations:
            emit(
                EventType.VALIDATION,
                result.name,
                {
                    "validation_id": result.validation_id,
                    "passed": result.passed,
                    "details": result.details,
                },
            )
            result.evidence_event_ids.append(record.events[-1].event_id)
        record.score = sum(v.passed for v in validations) / len(validations)
        record.status = RunStatus.PASSED if all(v.passed for v in validations) else RunStatus.FAILED
        record.finished_at = datetime.now(timezone.utc)
        emit(EventType.RUN_FINISHED, f"Run {record.status.value}", {"score": record.score})
        record.trace_complete = True
        record.trace_hash = self._trace_hash(record)
        return record

    def _validate(self, run_id: str, env: VirtualFileEnvironment) -> list[ValidationResult]:
        try:
            report = env.read_json("report.json")
            exists = ValidationResult(
                run_id=run_id,
                name="report_exists",
                passed=True,
                details="report.json exists and is valid JSON",
            )
        except Exception as exc:
            return [
                ValidationResult(
                    run_id=run_id,
                    name="report_exists",
                    passed=False,
                    details=f"report.json missing or invalid: {exc}",
                ),
                ValidationResult(
                    run_id=run_id,
                    name="report_values",
                    passed=False,
                    expected=EXPECTED_REPORT,
                    actual=None,
                    details="Cannot validate values without a valid report",
                ),
            ]
        values = ValidationResult(
            run_id=run_id,
            name="report_values",
            passed=report == EXPECTED_REPORT,
            expected=EXPECTED_REPORT,
            actual=report,
            details="Computed report must exactly match deterministic ground truth",
        )
        return [exists, values]

    def get(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    def list(self) -> list[RunRecord]:
        return list(self.runs.values())


run_service = RunService()
