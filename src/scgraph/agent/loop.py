"""The recorded tool loop: every model message, tool call and error is recorded as a step the moment it happens,
so the app can show a turn live.

It ends on the profile's end condition (`submit`: an accepted submission; `text`: a reply without tool calls),
after `NUDGES` nudges when a `submit` profile stops talking without submitting, on the turn limit, or on an LLM error. In
a `submit` profile's last `WRAP_UP_TURNS` turns the tool results say how many turns are left.
A reply cut off at the output limit runs none of its tool calls (the last input is incomplete); the model is asked
to send them again.
`scgraph.llm.loop.run_tool_loop` stays for the smoke test.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scgraph.agent.tools import Tool, ToolResult
from scgraph.llm import LLMClient, LLMError, Request, Response

MAX_TURNS = 18
MAX_TOKENS = 8000  # a plan for 60 components needs about 5000
WRAP_UP_TURNS = 3  # in the last turns of a submit profile, the tool results say how many are left
NUDGES = 2  # times a submit profile that stops talking without submitting is asked to submit
RESULT_CHARS = 6000
ENDS = ("submit", "text")


@dataclass
class LoopResult:
    status: str = "failed"  # done | failed
    text: str = ""  # the model's closing answer (text profiles)
    blocks: list[dict[str, Any]] = field(default_factory=list)  # from write tools, in order
    steps: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


def clip(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + " ...(cut)"


def run_loop(client: LLMClient, *, system: str, messages: list[dict[str, Any]], tools: list[Tool], end: str,
             nudge: str | None = None, on_step: Callable[[dict[str, Any]], None] | None = None,
             max_turns: int = MAX_TURNS, max_tokens: int = MAX_TOKENS) -> LoopResult:
    assert end in ENDS, end
    table = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    messages = list(messages)
    result = LoopResult()

    def step(kind: str, **data: Any) -> None:
        entry = {"kind": kind, "t": round(time.time(), 2), **data}
        result.steps.append(entry)
        if on_step:
            on_step(entry)

    def fail(error: str) -> LoopResult:
        result.error = error
        step("error", text=error)
        return result

    nudges = 0
    for turn_no in range(max_turns):
        try:
            response: Response = client.create(Request(messages=list(messages), system=system, tools=specs,
                                                       max_tokens=max_tokens))
        except LLMError as e:
            return fail(f"{type(e).__name__}: {e}")
        result.input_tokens += response.usage.input_tokens
        result.output_tokens += response.usage.output_tokens
        result.cost_usd += response.cost_usd
        messages.append({"role": "assistant", "content": response.content})
        if response.text.strip():
            step("thought", text=response.text.strip())
        calls = response.tool_calls
        if calls and response.stop_reason == "max_tokens":
            # the reply hit the output limit, so the last tool input is cut short: run nothing, ask again
            step("error", text="reply cut off at the output limit; asked again")
            text = "Your reply was cut off at the output limit. Send the call again, shorter: reasons of a few words."
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "is_error": True,
                                                          "content": text} for c in calls]})
            continue
        if not calls:
            if end == "text":
                result.status, result.text = "done", response.text.strip()
                return result
            if nudges >= NUDGES or nudge is None:
                return fail("the model stopped without submitting a plan")
            nudges += 1
            messages.append({"role": "user", "content": nudge})
            continue
        outputs, ended = [], False
        for call in calls:
            args = call.input or {}
            try:
                tool = table.get(call.name) or table.get(call.name.split(".")[0].strip())  # `component_details.x`
                if tool is None:
                    raise ValueError(f"unknown tool {call.name!r}")
                out = tool.run(args)
                out = out if isinstance(out, ToolResult) else ToolResult(out)
                error = False
            except (ValueError, TypeError, KeyError) as e:
                out, error = ToolResult(f"Error: {e}"), True
            value = out.value
            step("tool", name=call.name, input=args, result=value if isinstance(value, str | dict | list) else str(value),
                 error=error)
            if out.block is not None:
                result.blocks.append(out.block)
            ended = ended or out.ends
            block = {"type": "tool_result", "tool_use_id": call.id, "content": clip(value)}
            if error:
                block["is_error"] = True
            outputs.append(block)
        if ended and end == "submit":
            result.status = "done"
            return result
        left = max_turns - turn_no - 1
        if end == "submit" and 0 < left <= WRAP_UP_TURNS:
            # a model that keeps refining would otherwise run out of turns without a plan
            note = (f"{left} turn{'s' if left > 1 else ''} left: call submit_plan now with the cheapest plan you have "
                    "simulated (its simulation_id submits it as simulated).")
            outputs.append({"type": "text", "text": note})
            step("note", text=note)
        messages.append({"role": "user", "content": outputs})
    return fail(f"no valid plan after {max_turns} turns" if end == "submit" else f"no answer after {max_turns} turns")
