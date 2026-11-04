"""Per-model prices in USD per million tokens."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from scgraph.llm.errors import PriceMissingError


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    # Optional higher tier for prompts above `long_threshold` input tokens.
    long_threshold: int | None = None
    long_input: float | None = None
    long_output: float | None = None


PRICES: dict[str, Price] = {
    # Meta's published Muse Spark pricing.
    "muse-spark-1.1": Price(1.25, 4.25),
    # Self-hosted open weights: no per-token price (the GPU VM bills by the hour).
    "muse-glimmer": Price(0.0, 0.0),
}


def price_for(model: str, overrides: Mapping[str, tuple[float, float]] | None = None) -> Price:
    if overrides and model in overrides:
        return Price(*overrides[model])
    try:
        return PRICES[model]
    except KeyError:
        raise PriceMissingError(
            f"no price configured for model {model!r}; add it to scgraph.llm.pricing.PRICES "
            'or set SCGRAPH_LLM_PRICES=\'{"' + model + '": [input_usd_per_mtok, output_usd_per_mtok]}\''
        ) from None


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    overrides: Mapping[str, tuple[float, float]] | None = None,
) -> float:
    price = price_for(model, overrides)
    in_rate, out_rate = price.input, price.output
    if price.long_threshold is not None and input_tokens > price.long_threshold:
        in_rate, out_rate = price.long_input, price.long_output
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
