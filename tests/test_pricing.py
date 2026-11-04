import pytest

from fakes import FakeTransport, make_config
from scgraph.llm import LLMClient, PriceMissingError, Request
from scgraph.llm.pricing import PRICES, Price, cost_usd


def test_meta_cost():
    assert cost_usd("muse-spark-1.1", 1_000_000, 1_000_000) == pytest.approx(1.25 + 4.25)
    assert cost_usd("muse-spark-1.1", 2_000, 500) == pytest.approx((2_000 * 1.25 + 500 * 4.25) / 1e6)


def test_long_prompt_tier(monkeypatch):
    monkeypatch.setitem(PRICES, "tiered", Price(0.10, 0.50, long_threshold=100_000, long_input=0.50, long_output=2.50))
    assert cost_usd("tiered", 100_000, 1_000) == pytest.approx((100_000 * 0.10 + 1_000 * 0.50) / 1e6)
    assert cost_usd("tiered", 100_001, 1_000) == pytest.approx((100_001 * 0.50 + 1_000 * 2.50) / 1e6)


def test_override_wins():
    assert cost_usd("muse-spark-1.1", 1_000_000, 0, {"muse-spark-1.1": (2.0, 8.0)}) == pytest.approx(2.0)


def test_unpriced_model_fails_before_call(tmp_path):
    fake = FakeTransport()
    client = LLMClient(make_config(tmp_path), transport=fake)
    with pytest.raises(PriceMissingError, match="'mystery-model'.*SCGRAPH_LLM_PRICES"):
        client.create(Request(messages=[{"role": "user", "content": "hi"}], model="mystery-model"))
    assert fake.calls == 0
