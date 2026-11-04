"""Configuration from environment variables, an optional `.env` file and defaults."""

from __future__ import annotations

import getpass
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PROVIDERS = ("meta", "glimmer")
CACHE_MODES = ("record", "replay", "off")

DEFAULT_MODELS = {"meta": "muse-spark-1.1", "glimmer": "muse-glimmer"}
DEFAULT_BUDGETS_USD = {"meta": 5.0, "glimmer": 5.0}
# Self-hosted Muse Glimmer (open weights, served by vLLM); set MUSE_BASE_URL to your server.
DEFAULT_GLIMMER_BASE_URL = "http://localhost:8000/v1"
DEFAULT_DB_PATH = ".cache/llm.sqlite"
DEFAULT_GRAPH_DB_PATH = ".cache/graph.sqlite"
DEFAULT_REVIEW_THRESHOLD = 0.9
DEFAULT_EVIDENCE_DIR = ".cache/evidence"
DEFAULT_JOBS_DB_PATH = ".cache/jobs.sqlite"
DEFAULT_OPS_DB_PATH = ".cache/ops.sqlite"
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://localhost:3000")
DEFAULT_BUDGET_AUTO_USD = 2.0
DEFAULT_AGENT_SPEND_LIMIT_EUR = 250_000.0


class ConfigError(ValueError):
    pass


def load_dotenv(path: str | Path = ".env", environ: dict[str, str] | None = None) -> None:
    """Load KEY=VALUE lines from `path` into `environ` without overriding existing keys."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    provider: str = "meta"
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    cache_mode: str = "record"
    budgets_usd: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BUDGETS_USD))
    db_path: Path = Path(DEFAULT_DB_PATH)
    graph_db_path: Path = Path(DEFAULT_GRAPH_DB_PATH)
    run_id: str | None = None
    # Per-model (input, output) USD per million tokens, merged over the built-in table.
    price_overrides: dict[str, tuple[float, float]] = field(default_factory=dict)
    meta_api_key: str | None = None
    glimmer_api_key: str | None = None
    glimmer_base_url: str = DEFAULT_GLIMMER_BASE_URL
    # Facts at or above this confidence apply without review (when new or agreeing with the graph).
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD
    # Author of human changes when a command or request names none.
    author: str = field(default_factory=lambda: _os_user())
    evidence_dir: Path = Path(DEFAULT_EVIDENCE_DIR)
    jobs_db_path: Path = Path(DEFAULT_JOBS_DB_PATH)
    ops_db_path: Path = Path(DEFAULT_OPS_DB_PATH)
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # Events (a disruption created or updated) start the planner; off records and shows them only.
    agent_triggers: bool = True
    # Cumulative model spend allowed for event-started agent turns (ledger runs `auto-*`).
    budget_auto_usd: float = DEFAULT_BUDGET_AUTO_USD
    # Action costs per disruption above which the gate refuses to auto-apply a plan (shown on plans otherwise).
    agent_spend_limit_eur: float = DEFAULT_AGENT_SPEND_LIMIT_EUR

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env

        provider = env.get("SCGRAPH_LLM_PROVIDER") or "meta"
        if provider not in PROVIDERS:
            raise ConfigError(f"SCGRAPH_LLM_PROVIDER must be one of {PROVIDERS}, got {provider!r}")

        cache_mode = env.get("SCGRAPH_LLM_CACHE") or "record"
        if cache_mode not in CACHE_MODES:
            raise ConfigError(f"SCGRAPH_LLM_CACHE must be one of {CACHE_MODES}, got {cache_mode!r}")

        models = dict(DEFAULT_MODELS)
        budgets = dict(DEFAULT_BUDGETS_USD)
        for name in PROVIDERS:
            if model := env.get(f"SCGRAPH_{name.upper()}_MODEL"):
                models[name] = model
            var = f"SCGRAPH_BUDGET_{name.upper()}_USD"
            if value := env.get(var):
                try:
                    budgets[name] = float(value)
                except ValueError:
                    raise ConfigError(f"{var} must be a number, got {value!r}") from None

        threshold = DEFAULT_REVIEW_THRESHOLD
        if value := env.get("SCGRAPH_REVIEW_THRESHOLD"):
            try:
                threshold = float(value)
            except ValueError:
                raise ConfigError(f"SCGRAPH_REVIEW_THRESHOLD must be a number, got {value!r}") from None
            if not 0.0 <= threshold <= 1.0:
                raise ConfigError(f"SCGRAPH_REVIEW_THRESHOLD must be within 0..1, got {value!r}")
        origins = env.get("SCGRAPH_CORS_ORIGINS")
        triggers = (env.get("SCGRAPH_AGENT_TRIGGERS") or "on").strip().lower()
        if triggers not in ("on", "off", "1", "0", "true", "false"):
            raise ConfigError(f"SCGRAPH_AGENT_TRIGGERS must be on or off, got {triggers!r}")

        return cls(
            provider=provider,
            models=models,
            cache_mode=cache_mode,
            budgets_usd=budgets,
            db_path=Path(env.get("SCGRAPH_LLM_DB") or DEFAULT_DB_PATH),
            graph_db_path=Path(env.get("SCGRAPH_GRAPH_DB") or DEFAULT_GRAPH_DB_PATH),
            run_id=env.get("SCGRAPH_RUN_ID") or None,
            price_overrides=_parse_prices(env.get("SCGRAPH_LLM_PRICES")),
            meta_api_key=env.get("MODEL_API_KEY") or None,
            glimmer_api_key=env.get("MUSE_API_KEY") or None,
            glimmer_base_url=env.get("MUSE_BASE_URL") or DEFAULT_GLIMMER_BASE_URL,
            review_threshold=threshold,
            author=env.get("SCGRAPH_AUTHOR") or _os_user(),
            evidence_dir=Path(env.get("SCGRAPH_EVIDENCE_DIR") or DEFAULT_EVIDENCE_DIR),
            jobs_db_path=Path(env.get("SCGRAPH_JOBS_DB") or DEFAULT_JOBS_DB_PATH),
            ops_db_path=Path(env.get("SCGRAPH_OPS_DB") or DEFAULT_OPS_DB_PATH),
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()) if origins else DEFAULT_CORS_ORIGINS,
            agent_triggers=triggers in ("on", "1", "true"),
            budget_auto_usd=_number(env, "SCGRAPH_BUDGET_AUTO_USD", DEFAULT_BUDGET_AUTO_USD),
            agent_spend_limit_eur=_number(env, "SCGRAPH_AGENT_SPEND_LIMIT_EUR", DEFAULT_AGENT_SPEND_LIMIT_EUR),
        )

    def graph_store(self):
        """The main graph, with the review gate on."""
        from scgraph.graph.store import GraphStore

        return GraphStore(self.graph_db_path, review=True, threshold=self.review_threshold)


def _number(env: Mapping[str, str], var: str, default: float) -> float:
    value = env.get(var)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        raise ConfigError(f"{var} must be a number, got {value!r}") from None


def _os_user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


def _parse_prices(raw: str | None) -> dict[str, tuple[float, float]]:
    """Parse `{"model": [input_per_mtok, output_per_mtok], ...}`."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {model: (float(p[0]), float(p[1])) for model, p in data.items()}
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ConfigError(
            'SCGRAPH_LLM_PRICES must be JSON like {"model": [input_usd_per_mtok, output_usd_per_mtok]}'
        ) from None
