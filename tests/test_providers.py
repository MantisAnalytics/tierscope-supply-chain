import json

import anthropic
import httpx2
import pytest

from fakes import FakeTransport, make_config, user
from scgraph.llm import LLMClient, MissingCredentialsError
from scgraph.llm.providers import make_transport


@pytest.mark.parametrize(("provider", "model"), [("meta", "muse-spark-1.1"), ("glimmer", "muse-glimmer")])
def test_configured_model_used_per_provider(tmp_path, provider, model):
    fake = FakeTransport()
    client = LLMClient(make_config(tmp_path, SCGRAPH_LLM_PROVIDER=provider), transport=fake)
    client.create(user("hi"))
    assert fake.requests[0].model == model


def test_per_request_model_override(tmp_path):
    fake = FakeTransport()
    cfg = make_config(tmp_path, SCGRAPH_LLM_PRICES='{"other": [1, 1]}')
    LLMClient(cfg, transport=fake).create(user("hi").__class__(messages=user("hi").messages, model="other"))
    assert fake.requests[0].model == "other"


@pytest.mark.parametrize(("provider", "var"), [("meta", "MODEL_API_KEY"), ("glimmer", "MUSE_API_KEY")])
def test_missing_key_fails_before_call(tmp_path, provider, var):
    fake = FakeTransport()
    cfg = make_config(tmp_path, SCGRAPH_LLM_PROVIDER=provider, **{var: None})
    with pytest.raises(MissingCredentialsError, match=var):
        LLMClient(cfg, transport=fake).create(user("hi"))
    assert fake.calls == 0


def _sdk_capture(captured):
    def handler(req):
        captured.append(req)
        body = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": json.loads(req.content)["model"],
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "t1", "name": "lookup_part", "input": {"part_id": "P"}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
        return httpx2.Response(200, json=body)

    return anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))


def test_meta_adapter_wiring(tmp_path, monkeypatch):
    # The SDK reads this variable on its own; a stray value must never reach the Meta endpoint.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-stray-secret")
    captured = []
    transport = make_transport("meta", make_config(tmp_path), http_client=_sdk_capture(captured))
    resp = transport(user("hi").__class__(messages=user("hi").messages, model="muse-spark-1.1", seed=3))
    req = captured[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.meta.ai/v1/messages"
    assert req.headers["authorization"] == "Bearer test-meta-key"
    assert "x-api-key" not in req.headers
    assert "seed" not in body  # the run seed only keys the cache
    assert "thinking" not in body and "temperature" not in body
    assert resp.text == "hi"
    assert resp.tool_calls[0].input == {"part_id": "P"}
    assert resp.ignored_block_types == ("thinking",)
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (7, 3)


def test_glimmer_adapter_wiring(tmp_path):
    captured = []
    cfg = make_config(tmp_path, MUSE_BASE_URL="http://10.0.0.5:8000/v1")
    transport = make_transport("glimmer", cfg, http_client=_sdk_capture(captured))
    resp = transport(user("hi").__class__(messages=user("hi").messages, model="muse-glimmer"))
    req = captured[0]
    body = json.loads(req.content)
    assert str(req.url) == "http://10.0.0.5:8000/v1/messages"
    assert req.headers["authorization"] == "Bearer test-muse-key"
    assert (body["temperature"], body["top_p"], body["top_k"]) == (1.0, 0.95, 64)
    assert resp.ignored_block_types == ("thinking",)
