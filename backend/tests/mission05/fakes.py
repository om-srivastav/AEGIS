from collections import deque

from app.providers.model_contracts import Message, ModelResponse, Text, ToolCall


class ScriptedTransport:
    provider = "scripted"

    def __init__(self, responses, input_estimate=10):
        self.responses = deque(responses)
        self.requests = []
        self.count_requests = []
        self.input_estimate = input_estimate

    async def count_tokens(self, request, timeout, audit):
        self.count_requests.append(request)
        return self.input_estimate

    async def complete(self, request, timeout, audit):
        self.requests.append(request)
        item = self.responses.popleft()
        if isinstance(item, Exception):
            raise item
        return item


def response(*parts, finish="tools", input_tokens=10, output_tokens=10, resolved_model="offline-fixture-model"):
    return ModelResponse(
        message=Message("assistant", tuple(parts)),
        finish=finish,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        resolved_model=resolved_model,
        response_id="offline-fixture-response",
        request_id=None,
    )


def tool(call_id, name, arguments):
    return response(ToolCall(call_id, name, arguments))


def final_json(text):
    return response(Text(text), finish="stop")
