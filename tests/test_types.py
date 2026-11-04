import pytest

from scgraph.llm.errors import UnsupportedFeatureError
from scgraph.llm.types import Request, Response, Usage

TOOL = {"name": "lookup_part", "description": "Look up a part", "input_schema": {"type": "object", "properties": {}}}


def test_valid_subset_passes():
    Request(
        system="be brief",
        tools=[TOOL],
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "lookup_part", "input": {}}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "x", "is_error": False},
                    {"type": "text", "text": "and?"},
                ],
            },
        ],
    ).validate()


def test_non_subset_block_rejected_with_its_type():
    req = Request(messages=[{"role": "user", "content": [{"type": "image", "source": {}}]}])
    with pytest.raises(UnsupportedFeatureError, match="'image'"):
        req.validate()


def test_cache_control_rejected_by_name():
    req = Request(messages=[{"role": "user", "content": [{"type": "text", "text": "x", "cache_control": {}}]}])
    with pytest.raises(UnsupportedFeatureError, match="cache_control"):
        req.validate()


def test_provider_hosted_tool_rejected_with_name_and_type():
    req = Request(messages=[{"role": "user", "content": "x"}], tools=[{"type": "web_search_20260209", "name": "web_search"}])
    with pytest.raises(UnsupportedFeatureError, match=r"web_search.*web_search_20260209"):
        req.validate()


def test_tool_extra_field_rejected():
    req = Request(messages=[{"role": "user", "content": "x"}], tools=[{**TOOL, "strict": True}])
    with pytest.raises(UnsupportedFeatureError, match="strict"):
        req.validate()


def test_non_subset_request_parameter_rejected():
    with pytest.raises(TypeError, match="thinking"):
        Request(messages=[{"role": "user", "content": "x"}], thinking={"type": "adaptive"})


def test_system_blocks_rejected():
    req = Request(messages=[{"role": "user", "content": "x"}], system=[{"type": "text", "text": "s"}])
    with pytest.raises(UnsupportedFeatureError, match="system"):
        req.validate()


def test_response_round_trips_through_dict():
    resp = Response(
        model="m",
        stop_reason="tool_use",
        content=[{"type": "text", "text": "a"}, {"type": "tool_use", "id": "t", "name": "n", "input": {"k": 1}}],
        usage=Usage(3, 4),
        cost_usd=1.0,
    )
    back = Response.from_dict(resp.to_dict())
    assert back.text == "a"
    assert back.tool_calls[0].input == {"k": 1}
    assert back.usage == Usage(3, 4)
    assert back.cost_usd == 0.0
