from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.providers.model_contracts import Audit, FailureKind


@dataclass(frozen=True)
class VisibleTask:
    prompt: str


@dataclass(frozen=True)
class AgentFailure:
    kind: FailureKind
    code: str


@dataclass(frozen=True)
class AgentOutcome:
    execution_status: Literal["completed", "failed"]
    report_artifact_id: str | None
    failure: AgentFailure | None
    accounting: dict[str, Any]


class AgentAdapter(Protocol):
    name: str

    async def run(self, task: VisibleTask, tools: Any, audit: Audit) -> AgentOutcome: ...
