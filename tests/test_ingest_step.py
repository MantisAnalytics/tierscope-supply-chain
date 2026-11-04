import pytest

from fakes import FakeTransport, make_config, text_response, tool_response
from scgraph.ingest.step import StepError, call_tool
from scgraph.llm import LLMClient

TOOL = {"name": "submit_x", "description": "submit", "input_schema": {"type": "object", "properties": {}}}


def parse(data):
    if "n" not in data:
        raise ValueError("missing n")
    return data["n"]


def client(tmp_path, script):
    transport = FakeTransport(script)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


def test_valid_call(tmp_path):
    c, t = client(tmp_path, [tool_response("submit_x", {"n": 3})])
    result = call_tool(c, system="s", user={"a": 1}, tool=TOOL, parse=parse)
    assert result.value == 3 and t.calls == 1 and len(result.responses) == 1
    assert t.requests[0].tools == [TOOL] and t.requests[0].messages[0]["content"] == '{"a":1}'


def test_text_reply_then_valid_retry(tmp_path):
    c, t = client(tmp_path, [text_response("thinking..."), tool_response("submit_x", {"n": 4})])
    assert call_tool(c, system="s", user="u", tool=TOOL, parse=parse).value == 4
    retry = t.requests[1].messages
    assert retry[1]["role"] == "assistant" and "exactly one call" in retry[2]["content"][0]["text"]


def test_invalid_input_retry_carries_the_error(tmp_path):
    c, t = client(tmp_path, [tool_response("submit_x", {}, id="t1"), tool_response("submit_x", {"n": 5})])
    assert call_tool(c, system="s", user="u", tool=TOOL, parse=parse).value == 5
    [block] = t.requests[1].messages[2]["content"]
    assert block["tool_use_id"] == "t1" and block["is_error"] and "missing n" in block["content"]


def test_two_invalid_replies_fail_clearly(tmp_path):
    c, _ = client(tmp_path, [tool_response("submit_x", {}), text_response("no")])
    with pytest.raises(StepError, match="submit_x: model failed twice; the reply did not call submit_x"):
        call_tool(c, system="s", user="u", tool=TOOL, parse=parse)
