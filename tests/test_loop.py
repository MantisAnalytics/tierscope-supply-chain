from fakes import FakeTransport, make_config, text_response, tool_response, user
from scgraph.llm import LLMClient, run_tool_loop
from scgraph.llm.types import Response, Usage


def make_client(tmp_path, responses):
    fake = FakeTransport(responses)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=fake), fake


def last_tool_result(fake):
    return fake.requests[-1].messages[-1]["content"][0]


def test_round_trip(tmp_path):
    client, fake = make_client(tmp_path, [tool_response("lookup_part", {"part_id": "P1"}), text_response("Kestrel")])
    seen = []
    result = run_tool_loop(client, user("who?"), {"lookup_part": lambda inp: seen.append(inp) or {"supplier": "Kestrel"}})
    assert seen == [{"part_id": "P1"}]
    assert result.text == "Kestrel" and not result.hit_turn_limit
    assert last_tool_result(fake) == {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"supplier": "Kestrel"}'}
    assert [m["role"] for m in result.transcript] == ["user", "assistant", "user", "assistant"]
    assert result.usage == Usage(200, 40)
    assert len(result.responses) == 2


def test_unknown_tool_gets_error_result(tmp_path):
    client, fake = make_client(tmp_path, [tool_response("nope", {}), text_response("sorry")])
    result = run_tool_loop(client, user("x"), {})
    block = last_tool_result(fake)
    assert block["is_error"] is True and "unknown tool 'nope'" in block["content"]
    assert result.text == "sorry"


def test_raising_handler_gets_error_result(tmp_path):
    def boom(_):
        raise KeyError("P9")

    client, fake = make_client(tmp_path, [tool_response("lookup_part", {}), text_response("recovered")])
    result = run_tool_loop(client, user("x"), {"lookup_part": boom})
    block = last_tool_result(fake)
    assert block["is_error"] is True and "KeyError" in block["content"]
    assert result.text == "recovered"


def test_turn_limit(tmp_path):
    client, fake = make_client(tmp_path, [tool_response("t", {}, id=f"t{i}") for i in range(10)])
    calls = []
    result = run_tool_loop(client, user("x"), {"t": lambda inp: calls.append(1) or "ok"}, max_turns=3)
    assert result.hit_turn_limit
    assert fake.calls == 3 and len(calls) == 2
    assert result.transcript[-1]["role"] == "assistant"


def test_parallel_tool_calls_answered_in_one_message(tmp_path):
    two = Response(
        model="muse-spark-1.1",
        stop_reason="tool_use",
        content=[
            {"type": "tool_use", "id": "a", "name": "t", "input": {"n": 1}},
            {"type": "tool_use", "id": "b", "name": "t", "input": {"n": 2}},
        ],
    )
    client, fake = make_client(tmp_path, [two, text_response("done")])
    run_tool_loop(client, user("x"), {"t": lambda inp: inp["n"] * 10})
    results = fake.requests[-1].messages[-1]["content"]
    assert [(r["tool_use_id"], r["content"]) for r in results] == [("a", "10"), ("b", "20")]
