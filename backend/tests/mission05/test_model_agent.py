import asyncio

import pytest

from app.agents.adapter import VisibleTask
from app.agents.model_agent import ModelAgent
from app.agents.model_tools import ToolProblem
from app.providers.model_contracts import Audit, Limits, ToolCall

from .fakes import ScriptedTransport, response, tool


class MemoryTools:
    def __init__(self):
        self.calls = []
        self.report = None

    async def invoke(self, name, arguments, lease):
        lease.check()
        self.calls.append((name, arguments))
        if name == "list_files":
            return {"files": ["sales.csv"]}
        if name == "read_file":
            if arguments["path"] != "sales.csv":
                raise ToolProblem("not_found")
            return {"content": "amount\n2\n3\n"}
        if name == "write_report":
            lease.check()
            self.report = arguments["content"]
            return {"artifact_id": "offline-report-artifact"}
        raise AssertionError("controller dispatched an unknown tool")


def execute(responses, limits=None):
    events = []
    transport = ScriptedTransport(responses)
    tools = MemoryTools()
    agent = ModelAgent(transport, "offline-fixture-model", limits or Limits())
    outcome = asyncio.run(
        agent.run(
            VisibleTask("Write the sales report using the task report format."),
            tools,
            Audit(lambda kind, payload: events.append((kind, payload))),
        )
    )
    return outcome, tools, transport, events


def test_list_read_write_completes_execution():
    outcome, tools, transport, events = execute([
        tool("c1", "list_files", {}),
        tool("c2", "read_file", {"path": "sales.csv"}),
        tool("c3", "write_report", {"content": '{"total":5}'}),
    ])
    assert outcome.execution_status == "completed"
    assert tools.report == '{"total":5}'
    assert len(transport.requests) == 3
    assert outcome.failure is None
    assert any(kind == "agent.completed" for kind, _ in events)


@pytest.mark.parametrize("name,arguments,expected", [
    ("shell", {"command": "anything"}, "unknown_tool"),
    ("read_file", {"path": "../hidden"}, "invalid_virtual_path"),
    ("read_file", {"path": "C:\\hidden"}, "invalid_virtual_path"),
    ("read_file", {"path": 7}, "malformed_tool_arguments"),
    ("list_files", {"extra": True}, "malformed_tool_arguments"),
    ("write_report", {"content": "x", "path": "other"}, "malformed_tool_arguments"),
])
def test_rejected_call_never_reaches_sandbox(name, arguments, expected):
    outcome, tools, _, _ = execute([tool("c1", name, arguments)])
    assert outcome.execution_status == "failed"
    assert outcome.failure.code == expected
    assert tools.calls == []
    assert tools.report is None


def test_parallel_response_executes_nothing():
    outcome, tools, _, _ = execute([
        response(ToolCall("c1", "list_files", {}), ToolCall("c2", "write_report", {"content": "x"})),
    ])
    assert outcome.failure.code == "parallel_tool_calls_rejected"
    assert tools.calls == []


def test_missing_file_can_be_recovered():
    outcome, tools, _, events = execute([
        tool("c1", "read_file", {"path": "old.csv"}),
        tool("c2", "list_files", {}),
        tool("c3", "read_file", {"path": "sales.csv"}),
        tool("c4", "write_report", {"content": '{"total":5}'}),
    ])
    assert outcome.execution_status == "completed"
    assert tools.report == '{"total":5}'
    assert any(
        kind == "agent.tool.returned" and payload.get("failure_kind") == "tool"
        for kind, payload in events
    )


def test_step_limit_prevents_another_provider_step():
    outcome, tools, transport, _ = execute([tool("c1", "list_files", {})], Limits(max_steps=1))
    assert outcome.failure.code == "step_limit"
    assert len(transport.requests) == 1
    assert len(transport.count_requests) == 1


def test_total_usage_limit_prevents_tool_execution():
    oversized = response(
        ToolCall("c1", "write_report", {"content": "x"}),
        input_tokens=2_000,
        output_tokens=1,
    )
    outcome, tools, _, _ = execute([oversized], Limits(max_total_tokens=1_000))
    assert outcome.failure.code == "total_token_limit"
    assert tools.calls == []


def test_duplicate_tool_call_id_is_rejected_before_second_dispatch():
    outcome, tools, _, _ = execute([
        tool("same", "list_files", {}),
        tool("same", "write_report", {"content": '{"total":5}'}),
    ])
    assert outcome.failure.code == "duplicate_tool_call_id"
    assert len(tools.calls) == 1
