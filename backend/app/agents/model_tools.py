from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from app.envs.virtual_files import ToolTimeoutError, VirtualFileEnvironment
from app.models.schemas import EventType, TaskSpec
from app.providers.model_contracts import (
    Audit,
    Fault,
    ToolCall,
    ToolResult,
    ToolSpec,
    closed_validator,
    json_bytes,
    strict_json,
)


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOL_SPECS = (
    ToolSpec(
        "list_files",
        "List the files visible in this task's sandbox.",
        object_schema({}),
    ),
    ToolSpec(
        "read_file",
        "Read one task-visible file using its virtual relative path.",
        object_schema({"path": {"type": "string", "minLength": 1, "maxLength": 256}}),
    ),
    ToolSpec(
        "write_report",
        "Write the report to the task's fixed report destination.",
        object_schema({"content": {"type": "string", "minLength": 1, "maxLength": 32_768}}),
    ),
)

_INPUTS = {spec.name: closed_validator(spec.input_schema) for spec in TOOL_SPECS}
_OUTPUTS = {
    "list_files": closed_validator(
        object_schema(
            {
                "files": {
                    "type": "array",
                    "maxItems": 512,
                    "items": {"type": "string", "minLength": 1, "maxLength": 256},
                }
            }
        )
    ),
    "read_file": closed_validator(
        object_schema({"content": {"type": "string", "maxLength": 32_768}})
    ),
    "write_report": closed_validator(
        object_schema({"artifact_id": {"type": "string", "minLength": 1, "maxLength": 256}})
    ),
}


class ToolProblem(Exception):
    ALLOWED = {"not_found", "not_readable", "timeout", "invalid_report", "write_rejected"}

    def __init__(self, code: str):
        if code not in self.ALLOWED:
            raise ValueError("unsupported_public_tool_error")
        super().__init__(code)
        self.code = code


@dataclass
class CommitLease:
    deadline: float
    revoked: bool = False

    def check(self) -> None:
        if self.revoked or time.monotonic() >= self.deadline:
            raise Fault("policy_controller", "wall_time_limit")

    def revoke(self) -> None:
        self.revoked = True


class ToolPort(Protocol):
    async def invoke(self, name: str, arguments: dict[str, Any], lease: CommitLease) -> dict[str, Any]: ...


def valid_virtual_path(path: str) -> bool:
    if (
        path.startswith("/")
        or "\\" in path
        or ":" in path
        or any(ord(char) < 32 for char in path)
    ):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


class VirtualFileToolPort:
    """Narrow bridge from model tool calls into the existing virtual filesystem."""

    def __init__(self, env: VirtualFileEnvironment, task: TaskSpec, emit_native):
        self.env = env
        self.task = task
        self.emit_native = emit_native

    async def invoke(self, name: str, arguments: dict[str, Any], lease: CommitLease) -> dict[str, Any]:
        lease.check()
        self.emit_native(EventType.TOOL_CALL, name, arguments)
        try:
            if name == "list_files":
                listing = self.env.list_files(self.task.resource.allowed_scope_prefix)
                if not listing.get("complete", False):
                    raise ToolProblem("not_readable")
                data = {"files": [entry["path"] for entry in listing.get("entries", []) if entry.get("readable")]}
            elif name == "read_file":
                path = arguments["path"]
                try:
                    content = self.env.read_file(path)
                except FileNotFoundError:
                    raise ToolProblem("not_found") from None
                except ToolTimeoutError:
                    raise ToolProblem("timeout") from None
                data = {"content": content}
            elif name == "write_report":
                try:
                    parsed = strict_json(arguments["content"], max_bytes=32_768)
                except (ValueError, UnicodeError, RecursionError):
                    raise ToolProblem("invalid_report") from None
                if not isinstance(parsed, dict):
                    raise ToolProblem("invalid_report")
                lease.check()
                self.env.write_json("report.json", parsed)
                digest = hashlib.sha256(arguments["content"].encode("utf-8")).hexdigest()[:24]
                data = {"artifact_id": f"virtual-report:{digest}"}
            else:
                raise Fault("policy_controller", "unknown_tool")
        except ToolProblem as exc:
            self.emit_native(
                EventType.TOOL_RESULT,
                f"{name} failed",
                {"error_code": exc.code, "is_error": True},
            )
            raise

        lease.check()
        safe_payload = {"is_error": False}
        if name == "list_files":
            safe_payload["count"] = len(data["files"])
        elif name == "read_file":
            safe_payload["bytes"] = len(data["content"].encode("utf-8"))
        elif name == "write_report":
            safe_payload["artifact_id"] = data["artifact_id"]
        self.emit_native(EventType.TOOL_RESULT, f"{name} returned", safe_payload)
        return data


class ToolController:
    def __init__(self, port: ToolPort, meter: Any, audit: Audit):
        self.port = port
        self.meter = meter
        self.audit = audit
        self.seen_ids: set[str] = set()

    async def execute(self, call: ToolCall) -> ToolResult:
        self.audit("agent.tool.proposed", call=asdict(call))
        if call.name not in _INPUTS:
            raise Fault("policy_controller", "unknown_tool")
        if not isinstance(call.id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call.id) is None:
            raise Fault("model", "invalid_tool_call_id")
        if call.id in self.seen_ids:
            raise Fault("model", "duplicate_tool_call_id")
        if not _INPUTS[call.name].is_valid(call.arguments):
            raise Fault("model", "malformed_tool_arguments")
        if call.name == "read_file" and not valid_virtual_path(call.arguments["path"]):
            raise Fault("policy_controller", "invalid_virtual_path")
        if len(json_bytes(call.arguments)) > 65_536:
            raise Fault("policy_controller", "tool_argument_size_limit")

        self.meter.reserve_tool()
        self.seen_ids.add(call.id)
        lease = CommitLease(self.meter.deadline)
        self.audit("agent.tool.dispatched", call_id=call.id, tool=call.name)
        try:
            try:
                async with asyncio.timeout(self.meter.remaining()):
                    data = await self.port.invoke(call.name, call.arguments, lease)
                lease.check()
            except ToolProblem as exc:
                lease.check()
                result = ToolResult(id=call.id, data={"error": exc.code}, is_error=True)
                self.audit(
                    "agent.tool.returned",
                    tool=call.name,
                    result=asdict(result),
                    failure_kind="tool",
                )
                return result
            except TimeoutError:
                raise Fault("policy_controller", "wall_time_limit") from None

            if not _OUTPUTS[call.name].is_valid(data):
                raise Fault("provider_harness", "invalid_tool_result")
            if len(json_bytes(data)) > self.meter.limits.max_tool_result_bytes:
                raise Fault("policy_controller", "tool_result_size_limit")

            result = ToolResult(call.id, data)
            self.audit("agent.tool.returned", tool=call.name, result=asdict(result))
            return result
        finally:
            lease.revoke()
