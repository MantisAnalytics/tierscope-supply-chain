import json

import pytest

from fakes import FakeTransport, make_config, text_response, user
from scgraph.llm import CacheMissError, LLMClient
from scgraph.llm.cassette import Cassette


def test_record_writes_entry_then_replay_needs_no_key_or_transport(tmp_path):
    path = tmp_path / "c.json"
    live = FakeTransport([text_response("hello")])
    recorder = LLMClient(make_config(tmp_path), transport=live, cassette=Cassette(path, "record"))
    assert recorder.create(user("hi")).text == "hello"
    assert live.calls == 1
    entries = json.loads(path.read_text())
    assert len(entries) == 1 and next(iter(entries.values()))["response"]["content"][0]["text"] == "hello"

    replay_transport = FakeTransport()
    config = make_config(tmp_path, MODEL_API_KEY=None, SCGRAPH_LLM_DB=str(tmp_path / "other.sqlite"))
    player = LLMClient(config, transport=replay_transport, cassette=Cassette(path, "replay"))
    response = player.create(user("hi"))
    assert response.text == "hello" and response.cache_hit and response.cost_usd == 0.0
    assert replay_transport.calls == 0


def test_replay_miss_names_file_and_rerecord_command(tmp_path):
    path = tmp_path / "missing.json"
    client = LLMClient(make_config(tmp_path), transport=FakeTransport(), cassette=Cassette(path, "replay"))
    with pytest.raises(CacheMissError) as err:
        client.create(user("hi"))
    assert str(path) in str(err.value) and "SCGRAPH_CASSETTE=record" in str(err.value)


def test_record_keeps_only_used_entries_and_bypasses_cache(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"stale": {"provider": "meta", "request": {}, "response": {}}}))
    live = FakeTransport([text_response("one"), text_response("two")])
    client = LLMClient(make_config(tmp_path), transport=live, cassette=Cassette(path, "record"))
    first = client.create(user("a"))
    again = client.create(user("a"))  # same request again: the recorded answer, so replay stays consistent
    assert live.calls == 1 and again.text == first.text == "one" and again.cache_hit
    entries = json.loads(path.read_text())
    assert "stale" not in entries and len(entries) == 1


def test_record_respects_budget(tmp_path):
    from scgraph.llm import BudgetExceededError

    client = LLMClient(make_config(tmp_path, SCGRAPH_BUDGET_META_USD="0"), transport=FakeTransport(),
                       cassette=Cassette(tmp_path / "c.json", "record"))
    with pytest.raises(BudgetExceededError):
        client.create(user("a"))


def test_cassette_keys_ignore_an_unset_seed_and_split_on_a_set_one(tmp_path):
    path = tmp_path / "c.json"
    live = FakeTransport([text_response("plain"), text_response("seeded")])
    recorder = LLMClient(make_config(tmp_path), transport=live, cassette=Cassette(path, "record"))
    recorder.create(user("hi"))
    LLMClient(make_config(tmp_path), transport=live, cassette=recorder.cassette, run_seed=7).create(user("hi"))
    entries = json.loads(path.read_text())
    assert live.calls == 2 and len(entries) == 2
    assert sorted("seed" in e["request"] for e in entries.values()) == [False, True]
    player = LLMClient(make_config(tmp_path, MODEL_API_KEY=None), transport=FakeTransport(),
                       cassette=Cassette(path, "replay"))
    assert player.create(user("hi")).text == "plain"
    with pytest.raises(CacheMissError):
        LLMClient(make_config(tmp_path), transport=FakeTransport(), cassette=Cassette(path, "replay"),
                  run_seed=8).create(user("hi"))
