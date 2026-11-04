import pytest

from fakes import FakeTransport, make_config, text_response, user
from scgraph.llm import BudgetExceededError, CacheMissError, LLMClient
from scgraph.llm.types import Usage


def client_for(tmp_path, mode="record", transport=None, run_id="run-1", run_seed=None, **env):
    cfg = make_config(tmp_path, SCGRAPH_LLM_CACHE=mode, **env)
    return LLMClient(cfg, transport=transport or FakeTransport(), run_id=run_id, run_seed=run_seed)


# Cache modes


def test_record_miss_calls_and_stores(tmp_path):
    fake = FakeTransport([text_response("first")])
    client = client_for(tmp_path, transport=fake)
    resp = client.create(user("hi"))
    assert fake.calls == 1 and resp.text == "first" and not resp.cache_hit
    assert client.store.report()[0]["calls"] == 1


def test_record_hit_skips_transport(tmp_path):
    fake = FakeTransport([text_response("first")])
    client_for(tmp_path, transport=fake).create(user("hi"))
    # A new client, as in a later run, on the same database.
    fake2 = FakeTransport()
    resp = client_for(tmp_path, transport=fake2).create(user("hi"))
    assert fake2.calls == 0
    assert resp.text == "first" and resp.cache_hit and resp.cost_usd == 0.0


def test_replay_miss_errors_without_call(tmp_path):
    fake = FakeTransport()
    with pytest.raises(CacheMissError):
        client_for(tmp_path, mode="replay", transport=fake).create(user("hi"))
    assert fake.calls == 0


def test_replay_hit_works_without_keys(tmp_path):
    client_for(tmp_path, transport=FakeTransport([text_response("cached")])).create(user("hi"))
    fake = FakeTransport()
    resp = client_for(tmp_path, mode="replay", transport=fake, MODEL_API_KEY=None).create(user("hi"))
    assert resp.text == "cached" and fake.calls == 0


def test_off_always_calls_and_stores_nothing(tmp_path):
    fake = FakeTransport()
    client = client_for(tmp_path, mode="off", transport=fake)
    client.create(user("hi"))
    client.create(user("hi"))
    assert fake.calls == 2
    # Nothing cached: a record-mode client still misses.
    fake2 = FakeTransport()
    client_for(tmp_path, transport=fake2).create(user("hi"))
    assert fake2.calls == 1


# Ledger


def test_live_call_records_tokens_and_cost(tmp_path):
    fake = FakeTransport([text_response("x", usage=Usage(1_000, 200))])
    client = client_for(tmp_path, transport=fake)
    resp = client.create(user("hi"))
    [row] = client.store.ledger_rows()
    expected = (1_000 * 1.25 + 200 * 4.25) / 1e6
    assert row["run_id"] == "run-1"
    assert (row["provider"], row["model"]) == ("meta", "muse-spark-1.1")
    assert (row["input_tokens"], row["output_tokens"]) == (1_000, 200)
    assert row["cost_usd"] == pytest.approx(expected) and resp.cost_usd == pytest.approx(expected)
    assert row["cache_hit"] == 0


def test_cache_hit_recorded_free(tmp_path):
    client_for(tmp_path, transport=FakeTransport([text_response("x", usage=Usage(1_000, 200))])).create(user("hi"))
    client = client_for(tmp_path, run_id="run-2")
    client.create(user("hi"))
    [row] = client.store.ledger_rows("run-2")
    assert row["cache_hit"] == 1 and row["cost_usd"] == 0.0
    assert (row["input_tokens"], row["output_tokens"]) == (1_000, 200)


def test_off_mode_still_writes_ledger(tmp_path):
    client = client_for(tmp_path, mode="off")
    client.create(user("hi"))
    assert len(client.store.ledger_rows()) == 1


def test_report_per_provider_model_and_run(tmp_path):
    client_for(tmp_path, run_id="a").create(user("one"))
    client_for(tmp_path, run_id="b", SCGRAPH_LLM_PROVIDER="glimmer").create(user("two"))
    client = client_for(tmp_path, run_id="b")
    client.create(user("one"))  # cache hit
    everything = client.store.report()
    assert [(r["provider"], r["model"], r["calls"]) for r in everything] == [
        ("glimmer", "muse-glimmer", 1),
        ("meta", "muse-spark-1.1", 2),
    ]
    run_b = client.store.report("b")
    assert [(r["provider"], r["calls"], r["cache_hits"]) for r in run_b] == [("glimmer", 1, 0), ("meta", 1, 1)]


# Budget


def test_live_call_refused_at_cap(tmp_path):
    big = Usage(800_000, 0)  # $1.00 on Muse Spark
    client = client_for(tmp_path, transport=FakeTransport([text_response("x", usage=big)]), SCGRAPH_BUDGET_META_USD="1")
    client.create(user("first"))
    fake = FakeTransport()
    client._transport = fake
    with pytest.raises(BudgetExceededError, match=r"\$1\.0000 of \$1\.00"):
        client.create(user("second"))
    assert fake.calls == 0


def test_cache_hit_allowed_with_exhausted_budget(tmp_path):
    client = client_for(tmp_path, transport=FakeTransport([text_response("x", usage=Usage(800_000, 0))]), SCGRAPH_BUDGET_META_USD="1")
    client.create(user("first"))
    for mode in ("record", "replay"):
        resp = client_for(tmp_path, mode=mode, SCGRAPH_BUDGET_META_USD="1").create(user("first"))
        assert resp.cache_hit


def test_budget_is_per_provider(tmp_path):
    client_for(tmp_path, transport=FakeTransport([text_response("x", usage=Usage(800_000, 0))]), SCGRAPH_BUDGET_META_USD="1").create(user("x"))
    fake = FakeTransport()
    client_for(tmp_path, transport=fake, SCGRAPH_LLM_PROVIDER="glimmer").create(user("x"))
    assert fake.calls == 1


# Run seed


def test_key_without_seed_is_unchanged(tmp_path):
    import hashlib

    from scgraph.llm.store import canonical_json, request_key
    from scgraph.llm.types import Request

    request = Request(messages=[{"role": "user", "content": "hi"}], model="m")
    legacy = {"provider": "meta", "model": "m", "system": None, "messages": request.messages, "tools": [],
              "max_tokens": 4096, "temperature": None}
    assert request_key("meta", request) == hashlib.sha256(canonical_json(legacy).encode()).hexdigest()
    assert "seed" not in request.to_dict()
    assert request_key("meta", Request(messages=request.messages, model="m", seed=0)) != request_key("meta", request)


def test_seeded_request_misses_and_unseeded_still_hits(tmp_path):
    fake = FakeTransport([text_response("plain"), text_response("seed 0"), text_response("seed 1")])
    client_for(tmp_path, transport=fake).create(user("hi"))
    assert client_for(tmp_path, transport=fake, run_seed=0).create(user("hi")).text == "seed 0"
    assert client_for(tmp_path, transport=fake, run_seed=1).create(user("hi")).text == "seed 1"
    assert fake.calls == 3
    assert client_for(tmp_path, transport=fake, run_seed=0).create(user("hi")).cache_hit
    again = client_for(tmp_path, transport=fake).create(user("hi"))
    assert again.cache_hit and again.text == "plain" and fake.calls == 3
