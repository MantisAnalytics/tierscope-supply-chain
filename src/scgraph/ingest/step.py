"""One model step = one client-side tool the model must call once, with validated input."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from scgraph.llm import LLMClient, Request, Response

T = TypeVar("T")


class StepError(RuntimeError):
    pass


@dataclass
class StepResult[T]:
    value: T
    responses: list[Response] = field(default_factory=list)


def call_tool(
    client: LLMClient,
    *,
    system: str,
    user: str | dict[str, Any],
    tool: dict[str, Any],
    parse: Callable[[dict[str, Any]], T],
    max_tokens: int = 8192,
    lenient: Callable[[dict[str, Any]], T] | None = None,
) -> StepResult[T]:
    """Ask for exactly one call to `tool`; `parse` validates its input and raises ValueError when it is wrong.

    A reply without the tool call, or with input `parse` rejects, gets one retry with the problem stated.
    `lenient`, if given, parses the retry's input instead, to keep the usable part of an imperfect answer.
    """
    content = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False, separators=(",", ":"))
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    responses: list[Response] = []
    problem = ""
    for attempt in (1, 2):
        response = client.create(Request(system=system, messages=list(messages), tools=[tool], max_tokens=max_tokens))
        responses.append(response)
        calls = [c for c in response.tool_calls if c.name == tool["name"]]
        if calls:
            try:
                return StepResult((lenient if attempt == 2 and lenient else parse)(calls[0].input), responses)
            except (ValueError, KeyError, TypeError) as e:
                problem = f"the input of {tool['name']} was invalid: {e}"
                retry = [{"type": "tool_result", "tool_use_id": calls[0].id, "is_error": True,
                          "content": f"Invalid input: {e}. Call {tool['name']} again with a corrected input."}]
        else:
            problem = f"the reply did not call {tool['name']}"
            retry = [{"type": "text", "text": f"Reply with exactly one call to the tool {tool['name']}."}]
        if attempt == 1:
            # Tool results must answer every tool_use block of the assistant turn.
            others = [c for c in response.tool_calls if c not in calls[:1]]
            retry = [{"type": "tool_result", "tool_use_id": c.id, "is_error": True, "content": "Not used."}
                     for c in others] + retry
            messages += [{"role": "assistant", "content": response.content}, {"role": "user", "content": retry}]
    raise StepError(f"{tool['name']}: model failed twice; {problem}")
