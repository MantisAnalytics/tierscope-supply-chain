from pathlib import Path

import pytest

from scgraph.config import Config, ConfigError, load_dotenv


def test_defaults_apply_with_empty_env():
    cfg = Config.from_env({})
    assert cfg.provider == "meta"
    assert cfg.models == {"meta": "muse-spark-1.1", "glimmer": "muse-glimmer"}
    assert cfg.cache_mode == "record"
    assert cfg.budgets_usd == {"meta": 5.0, "glimmer": 5.0}
    assert cfg.db_path == Path(".cache/llm.sqlite")
    assert cfg.graph_db_path == Path(".cache/graph.sqlite")
    assert cfg.meta_api_key is None and cfg.glimmer_api_key is None


def test_env_overrides_win():
    cfg = Config.from_env(
        {
            "SCGRAPH_LLM_PROVIDER": "glimmer",
            "SCGRAPH_META_MODEL": "muse-other",
            "SCGRAPH_LLM_CACHE": "replay",
            "SCGRAPH_BUDGET_META_USD": "42.5",
            "SCGRAPH_LLM_PRICES": '{"muse-other": [1, 2]}',
            "SCGRAPH_RUN_ID": "run-1",
            "MODEL_API_KEY": "mk",
            "SCGRAPH_GRAPH_DB": "/tmp/g.sqlite",
        }
    )
    assert cfg.graph_db_path == Path("/tmp/g.sqlite")
    assert cfg.provider == "glimmer"
    assert cfg.models["meta"] == "muse-other"
    assert cfg.models["glimmer"] == "muse-glimmer"
    assert cfg.cache_mode == "replay"
    assert cfg.budgets_usd == {"meta": 42.5, "glimmer": 5.0}
    assert cfg.price_overrides == {"muse-other": (1.0, 2.0)}
    assert cfg.run_id == "run-1"
    assert cfg.meta_api_key == "mk"


@pytest.mark.parametrize(
    "env",
    [
        {"SCGRAPH_LLM_PROVIDER": "openai"},
        {"SCGRAPH_LLM_CACHE": "always"},
        {"SCGRAPH_BUDGET_META_USD": "lots"},
        {"SCGRAPH_LLM_PRICES": "[1, 2]"},
    ],
)
def test_invalid_values_rejected(env):
    with pytest.raises(ConfigError):
        Config.from_env(env)


def test_only_muse_providers_accepted():
    with pytest.raises(ConfigError, match=r"\('meta', 'glimmer'\).*'claude'"):
        Config.from_env({"SCGRAPH_LLM_PROVIDER": "claude"})


def test_dotenv_does_not_override_existing(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nA=from_file\nexport B='quoted'\n\nC=\n")
    environ = {"A": "from_env"}
    load_dotenv(path, environ)
    assert environ == {"A": "from_env", "B": "quoted", "C": ""}


def test_dotenv_missing_file_is_fine(tmp_path):
    environ: dict[str, str] = {}
    load_dotenv(tmp_path / "nope", environ)
    assert environ == {}


def test_review_settings():
    from scgraph.config import Config, ConfigError

    config = Config.from_env({"SCGRAPH_REVIEW_THRESHOLD": "0.8", "SCGRAPH_AUTHOR": "ana",
                              "SCGRAPH_EVIDENCE_DIR": "/tmp/ev", "SCGRAPH_CORS_ORIGINS": "http://a, http://b"})
    assert (config.review_threshold, config.author, str(config.evidence_dir)) == (0.8, "ana", "/tmp/ev")
    assert config.cors_origins == ("http://a", "http://b")
    defaults = Config.from_env({})
    assert defaults.review_threshold == 0.9 and defaults.author
    for bad in ("x", "1.5"):
        with pytest.raises(ConfigError, match="SCGRAPH_REVIEW_THRESHOLD"):
            Config.from_env({"SCGRAPH_REVIEW_THRESHOLD": bad})
