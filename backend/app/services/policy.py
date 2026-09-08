from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from app.models.schemas import AgentPolicy, EventType, ResourceReobservationPolicy, TaskSpec


def stable_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def compute_policy_hash(policy: AgentPolicy | ResourceReobservationPolicy) -> str:
    if isinstance(policy, AgentPolicy):
        behavior = {"resource_reobservation": policy.resource_reobservation.model_dump(mode="json")}
    else:
        behavior = policy.model_dump(mode="json")
    return stable_hash(behavior)


def make_policy(*, enabled: bool, parent_policy_id: str | None = None, version: int = 1) -> AgentPolicy:
    rr = ResourceReobservationPolicy(enabled=enabled)
    policy = AgentPolicy(version=version, parent_policy_id=parent_policy_id, resource_reobservation=rr)
    return policy.model_copy(update={"policy_hash": compute_policy_hash(policy)})


class ResourceRecoveryController:
    """Enforces one bounded re-observation after a missing declared resource."""

    def __init__(self, policy: AgentPolicy, task: TaskSpec, emit) -> None:
        self.policy = policy
        self.task = task
        self.emit = emit
        self.discovery_calls = 0
        self.recovery_read_calls = 0

    def recover_missing_resource(self, env, failed_path: str) -> str:
        cfg = self.policy.resource_reobservation
        if not cfg.enabled:
            raise FileNotFoundError(failed_path)
        if failed_path != self.task.resource.original_path:
            raise FileNotFoundError(failed_path)
        if self.discovery_calls >= cfg.max_discovery_calls_per_run:
            raise FileNotFoundError(failed_path)

        self.emit(EventType.RECOVERY, "Recovery episode started", {
            "trigger_error": "FILE_NOT_FOUND",
            "failed_path": failed_path,
            "effective_policy_hash": self.policy.policy_hash,
        })
        self.emit(EventType.TOOL_CALL, "list_files", {"scope": self.task.resource.allowed_scope_prefix})
        self.discovery_calls += 1
        listing = env.list_files(self.task.resource.allowed_scope_prefix)
        self.emit(EventType.TOOL_RESULT, "list_files returned", {
            "complete": listing["complete"],
            "count": len(listing["entries"]),
            "entries": listing["entries"],
        })
        if cfg.require_complete_listing and not listing["complete"]:
            self.emit(EventType.RECOVERY, "Recovery failed", {"reason": "incomplete_listing"})
            raise FileNotFoundError(failed_path)

        matches = [
            e for e in listing["entries"]
            if e.get("readable") and e.get("resource_id") == self.task.resource.resource_id
        ]
        if len(matches) == 0:
            self.emit(EventType.RECOVERY, "Recovery failed", {"reason": "no_match", "candidate_count": 0})
            raise FileNotFoundError(failed_path)
        if len(matches) > 1:
            self.emit(EventType.RECOVERY, "Recovery failed", {"reason": "ambiguous", "candidate_count": len(matches)})
            raise FileNotFoundError(failed_path)

        selected = matches[0]
        selected_path = selected["path"]
        if self.recovery_read_calls >= cfg.max_recovery_read_calls_per_run:
            raise FileNotFoundError(failed_path)
        self.emit(EventType.RECOVERY, "Recovery candidate selected", {
            "candidate_count": 1,
            "selected_path": selected_path,
            "selected_resource_id": selected["resource_id"],
        })
        self.emit(EventType.TOOL_CALL, "read_file", {"path": selected_path, "recovery": True})
        self.recovery_read_calls += 1
        raw = env.read_file(selected_path)
        if env.get_resource_identity(selected_path) != self.task.resource.resource_id:
            self.emit(EventType.RECOVERY, "Recovery failed", {"reason": "identity_changed"})
            raise FileNotFoundError(failed_path)
        self.emit(EventType.TOOL_RESULT, "Recovered resource returned", {
            "path": selected_path,
            "bytes": len(raw),
            "resource_id": self.task.resource.resource_id,
        })
        self.emit(EventType.RECOVERY, "Recovery succeeded", {
            "terminal_status": "success",
            "selected_path": selected_path,
        })
        return raw
