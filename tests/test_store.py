from dataclasses import replace

import pytest

from scgraph.llm.store import Store, request_key
from scgraph.llm.types import Request, Response, Usage

BASE = Request(
    system="sys",
    messages=[{"role": "user", "content": "hi"}],
    tools=[{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
    model="muse-spark-1.1",
)


def test_identical_requests_share_a_key():
    same = Request(
        system="sys",
        messages=[{"content": "hi", "role": "user"}],
        tools=[{"input_schema": {"type": "object"}, "description": "d", "name": "t"}],
        model="muse-spark-1.1",
    )
    assert request_key("meta", BASE) == request_key("meta", same)


@pytest.mark.parametrize(
    "changed",
    [
        replace(BASE, system="other"),
        replace(BASE, messages=[{"role": "user", "content": "hello"}]),
        replace(BASE, tools=[]),
        replace(BASE, model="muse-glimmer"),
        replace(BASE, max_tokens=10),
        replace(BASE, temperature=0.5),
    ],
)
def test_any_change_changes_the_key(changed):
    assert request_key("meta", changed) != request_key("meta", BASE)


def test_provider_is_part_of_the_key():
    assert request_key("glimmer", BASE) != request_key("meta", BASE)


def test_store_persists_across_connections(tmp_path):
    path = tmp_path / "sub" / "llm.sqlite"
    store = Store(path)
    resp = Response(model="m", stop_reason="end_turn", content=[{"type": "text", "text": "x"}], usage=Usage(1, 2))
    store.put_response("k", "meta", BASE, resp)
    store.add_ledger(run_id="r", provider="meta", model="m", input_tokens=1, output_tokens=2, cost_usd=0.5, cache_hit=False)
    store.close()
    again = Store(path)
    assert again.get_response("k").text == "x"
    assert again.spend("meta") == 0.5
    assert again.spend("glimmer") == 0.0
