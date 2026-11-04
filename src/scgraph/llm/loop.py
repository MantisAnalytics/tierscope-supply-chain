"""Tool-calling loop over the client. Each turn is a normal client call."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from scgraph.llm.types import Request, Response, Usage

Handler = Callable[[dict[str, Any]], Any]


@dataclass
class LoopResult:
    text: str
    # The full message list: the request's messages plus every assistant turn and tool result.
    transcript: list[dict[str, Any]]
    responses: list[Response] = field(default_factory=list)
    hit_turn_limit: bool = False

    @property
    def usage(self) -> Usage:
        return Usage(
            sum(r.usage.input_tokens for r in self.responses),
            sum(r.usage.output_tokens for r in self.responses),
        )

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.responses)


def run_tool_loop(client: Any, request: Request, handlers: Mapping[str, Handler], max_turns: int = 8) -> LoopResult:
    """Call the model, run requested tools through `handlers`, send results back, repeat.

    Stops when a response has no tool calls, or after `max_turns` model calls. Unknown tools and
    handler exceptions become `is_error` tool results so the model can recover.
    """
    messages = list(request.messages)
    responses: list[Response] = []
    for turn in range(1, max_turns + 1):
        response = client.create(replace(request, messages=list(messages)))
        responses.append(response)
        messages.append({"role": "assistant", "content": response.content})
        calls = response.tool_calls
        if not calls:
            return LoopResult(response.text, messages, responses)
        if turn == max_turns:
            break
        messages.append({"role": "user", "content": [_run_tool(call, handlers) for call in calls]})
    return LoopResult(responses[-1].text, messages, responses, hit_turn_limit=True)


def _run_tool(call: Any, handlers: Mapping[str, Handler]) -> dict[str, Any]:
    handler = handlers.get(call.name)
    if handler is None:
        return _result(call.id, f"Error: unknown tool {call.name!r}", is_error=True)
    try:
        value = handler(call.input)
    except Exception as e:  # noqa: BLE001 - any handler failure is reported to the model
        return _result(call.id, f"Error: {type(e).__name__}: {e}", is_error=True)
    return _result(call.id, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


def _result(tool_use_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block
