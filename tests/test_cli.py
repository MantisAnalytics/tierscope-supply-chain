import pytest

from fakes import FakeTransport, text_response, tool_response
from scgraph.cli import main
from scgraph.llm.errors import ProviderError
from scgraph.llm.store import Store
from scgraph.llm.types import Usage


@pytest.fixture
def env(tmp_path, monkeypatch):
    for var in ("SCGRAPH_LLM_PROVIDER", "SCGRAPH_LLM_CACHE", "SCGRAPH_RUN_ID", "SCGRAPH_LLM_PRICES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MUSE_API_KEY", "k")
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setenv("SCGRAPH_LLM_DB", str(tmp_path / "llm.sqlite"))
    return monkeypatch


def run(argv, transport=None):
    return main(argv, transport=transport, load_env=lambda: None)


def passing_script(model="muse-spark-1.1"):
    return FakeTransport(
        [
            tool_response("lookup_part", {"part_id": "P-1042"}, model=model, usage=Usage(500, 40)),
            text_response("Kestrel Forge supplies it.", model=model, usage=Usage(600, 10)),
        ]
    )


@pytest.mark.parametrize("provider", ["meta", "glimmer"])
def test_smoke_pass(env, capsys, provider):
    model = {"meta": "muse-spark-1.1", "glimmer": "muse-glimmer"}[provider]
    assert run(["llm", "smoke", "--provider", provider], passing_script(model)) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and model in out and "1100 in / 50 out" in out and "cost:" in out


def test_smoke_bypasses_cache(env):
    assert run(["llm", "smoke", "--provider", "meta"], passing_script()) == 0
    again = passing_script()
    assert run(["llm", "smoke", "--provider", "meta"], again) == 0
    assert again.calls == 2


@pytest.mark.parametrize(
    "script",
    [
        [text_response("I think it's Acme.")],  # no tool call
        [ProviderError("meta: HTTP 401: invalid api key")],  # auth failure
        [tool_response("lookup_part", {"part_id": "P-1042"}), text_response("No idea.")],  # wrong answer
    ],
)
def test_smoke_fail_exits_nonzero(env, capsys, script):
    assert run(["llm", "smoke", "--provider", "meta"], FakeTransport(script)) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "reason:" in out


def test_smoke_missing_key_fails(env, capsys):
    env.delenv("MODEL_API_KEY")
    fake = FakeTransport()
    assert run(["llm", "smoke", "--provider", "meta"], fake) == 1
    assert "MODEL_API_KEY" in capsys.readouterr().out and fake.calls == 0


def test_smoke_respects_budget(env, capsys):
    env.setenv("SCGRAPH_BUDGET_META_USD", "0")
    fake = FakeTransport()
    assert run(["llm", "smoke", "--provider", "meta"], fake) == 1
    assert "budget reached" in capsys.readouterr().out and fake.calls == 0


def test_spend_report(env, capsys):
    env.setenv("SCGRAPH_RUN_ID", "r1")
    run(["llm", "smoke", "--provider", "meta"], passing_script())
    capsys.readouterr()
    assert run(["llm", "spend"]) == 0
    out = capsys.readouterr().out
    assert "muse-spark-1.1" in out and "of $5.00 budget" in out
    assert run(["llm", "spend", "--run", "r1"]) == 0
    assert "run r1" in capsys.readouterr().out
    assert run(["llm", "spend", "--run", "other"]) == 0
    assert "no entries" in capsys.readouterr().out


def test_spend_lists_provider_without_budget(env, capsys, tmp_path):
    # A ledger written by an older build can name a provider this one no longer has.
    Store(tmp_path / "llm.sqlite").add_ledger(run_id="r", provider="retired", model="old-model", input_tokens=1,
                                              output_tokens=2, cost_usd=0.5, cache_hit=False)
    assert run(["llm", "spend"]) == 0
    out = capsys.readouterr().out
    assert "retired: $0.5000\n" in out and "old-model" in out


def test_spend_without_ledger(env, capsys):
    assert run(["llm", "spend"]) == 0
    assert "no ledger yet" in capsys.readouterr().out
