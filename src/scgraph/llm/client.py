"""The LLM client: validation, cache, ledger and budget around a provider transport."""

from __future__ import annotations

import uuid
from dataclasses import replace

from scgraph.config import Config
from scgraph.llm import pricing
from scgraph.llm.cassette import Cassette
from scgraph.llm.errors import BudgetExceededError, CacheMissError, MissingCredentialsError
from scgraph.llm.providers import KEY_VARS, Transport, api_key_for, make_transport
from scgraph.llm.store import Store, request_key
from scgraph.llm.types import Request, Response


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class LLMClient:
    def __init__(
        self,
        config: Config,
        *,
        transport: Transport | None = None,
        store: Store | None = None,
        run_id: str | None = None,
        cache_mode: str | None = None,
        cassette: Cassette | None = None,
        run_seed: int | None = None,
    ) -> None:
        self.config = config
        self.provider = config.provider
        self.cache_mode = cache_mode or config.cache_mode
        self.run_id = run_id or config.run_id or new_run_id()
        self.store = store or Store(config.db_path)
        self._transport = transport
        self.cassette = cassette
        # Set on every request that has no seed of its own (see `Request.seed`).
        self.run_seed = run_seed

    @property
    def default_model(self) -> str:
        return self.config.models[self.provider]

    def create(self, request: Request) -> Response:
        request.validate()
        request = replace(request, model=request.model or self.default_model,
                          seed=request.seed if request.seed is not None else self.run_seed)
        key = request_key(self.provider, request)

        if self.cassette is not None and (self.cassette.mode == "replay" or self.cassette.recorded(key)):
            recorded = self.cassette.recorded(key) or self.cassette.get(key)
            self._record(request.model, recorded.usage.input_tokens, recorded.usage.output_tokens, 0.0, True)
            return replace(recorded, cache_hit=True)

        if self.cache_mode != "off" and self.cassette is None:
            cached = self.store.get_response(key)
            if cached is not None:
                self._record(request.model, cached.usage.input_tokens, cached.usage.output_tokens, 0.0, True)
                return replace(cached, cache_hit=True)
            if self.cache_mode == "replay":
                raise CacheMissError(f"replay mode: no cached response for this {self.provider} request (key {key[:12]})")

        self._check_live_call(request.model)
        response = self._live_transport()(request)
        cost = pricing.cost_usd(
            request.model, response.usage.input_tokens, response.usage.output_tokens, self.config.price_overrides
        )
        # Ledger first, so a crash during later processing still counts the spend.
        self._record(request.model, response.usage.input_tokens, response.usage.output_tokens, cost, False)
        if self.cache_mode == "record":
            self.store.put_response(key, self.provider, request, response)
        if self.cassette is not None:
            self.cassette.put(key, self.provider, request, response)
        return replace(response, cost_usd=cost)

    def _check_live_call(self, model: str) -> None:
        if not api_key_for(self.provider, self.config):
            raise MissingCredentialsError(f"{KEY_VARS[self.provider]} is not set (needed for provider {self.provider!r})")
        pricing.price_for(model, self.config.price_overrides)
        cap = self.config.budgets_usd[self.provider]
        spent = self.store.spend(self.provider)
        if spent >= cap:
            raise BudgetExceededError(
                f"{self.provider} budget reached: spent ${spent:.4f} of ${cap:.2f} cap "
                f"(raise SCGRAPH_BUDGET_{self.provider.upper()}_USD to continue)"
            )

    def _live_transport(self) -> Transport:
        if self._transport is None:
            self._transport = make_transport(self.provider, self.config)
        return self._transport

    def _record(self, model: str, input_tokens: int, output_tokens: int, cost: float, cache_hit: bool) -> None:
        self.store.add_ledger(
            run_id=self.run_id,
            provider=self.provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            cache_hit=cache_hit,
        )
