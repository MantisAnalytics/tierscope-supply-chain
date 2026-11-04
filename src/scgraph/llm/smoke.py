"""Connectivity smoke test: one tool-call round trip against a provider, cache off."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from scgraph.config import Config
from scgraph.llm.client import LLMClient
from scgraph.llm.errors import LLMError
from scgraph.llm.loop import run_tool_loop
from scgraph.llm.providers import Transport
from scgraph.llm.store import Store
from scgraph.llm.types import Request

PART_ID = "P-1042"
SUPPLIER = "Kestrel Forge"

LOOKUP_PART = {
    "name": "lookup_part",
    "description": "Look up a part in the parts catalogue by its id. Returns name, supplier and lead time.",
    "input_schema": {
        "type": "object",
        "properties": {"part_id": {"type": "string", "description": "Part id, e.g. P-0001"}},
        "required": ["part_id"],
    },
}

SMOKE_REQUEST = Request(
    system="You answer questions about parts. Always use the lookup_part tool; never guess.",
    messages=[{"role": "user", "content": f"Who supplies part {PART_ID}? Answer with the supplier name."}],
    tools=[LOOKUP_PART],
    max_tokens=1024,
)


def run_smoke(
    config: Config,
    provider: str,
    *,
    transport: Transport | None = None,
    store: Store | None = None,
    run_id: str | None = None,
    out: Callable[[str], None] = print,
) -> int:
    config = replace(config, provider=provider)
    client = LLMClient(config, transport=transport, store=store, run_id=run_id, cache_mode="off")
    calls: list[dict] = []

    def lookup_part(inp: dict) -> dict:
        calls.append(inp)
        if inp.get("part_id") != PART_ID:
            raise KeyError(f"unknown part {inp.get('part_id')!r}")
        return {"part_id": PART_ID, "name": "Brake caliper bracket", "supplier": SUPPLIER, "lead_time_days": 14}

    started = time.monotonic()
    reason = None
    result = None
    try:
        result = run_tool_loop(client, SMOKE_REQUEST, {"lookup_part": lookup_part}, max_turns=4)
    except LLMError as e:
        reason = str(e)
    latency = time.monotonic() - started

    if result is not None:
        if not calls:
            reason = "model did not call lookup_part"
        elif not any(c.get("part_id") == PART_ID for c in calls):
            reason = f"lookup_part called with unexpected input {calls!r}"
        elif result.hit_turn_limit:
            reason = "turn limit reached without a final answer"
        elif SUPPLIER.lower() not in result.text.lower():
            reason = f"final answer does not name the supplier: {result.text[:200]!r}"

    model = result.responses[-1].model if result and result.responses else client.default_model
    out(f"provider: {provider}")
    out(f"model:    {model}")
    out(f"result:   {'PASS' if reason is None else 'FAIL'}")
    if reason:
        out(f"reason:   {reason}")
    out(f"latency:  {latency:.2f}s")
    if result is not None:
        ignored = sorted({t for r in result.responses for t in r.ignored_block_types})
        out(f"turns:    {len(result.responses)}")
        out(f"tokens:   {result.usage.input_tokens} in / {result.usage.output_tokens} out")
        out(f"cost:     ${result.cost_usd:.6f}")
        if ignored:
            out(f"ignored blocks: {', '.join(ignored)}")
    out(f"run:      {client.run_id}")
    return 0 if reason is None else 1
