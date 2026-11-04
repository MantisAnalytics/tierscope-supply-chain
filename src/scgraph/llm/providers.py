"""Provider adapters for Messages API endpoints, built on the `anthropic` SDK.

Muse Spark (Meta Model API) and self-hosted Muse Glimmer (vLLM) both serve the Messages API.

A transport is a callable `(Request) -> Response`; the request's model is already resolved.
Provider-specific quirks live here and nowhere else.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import anthropic

from scgraph.config import Config
from scgraph.llm.errors import BillingError, PolicyError, ProviderError
from scgraph.llm.types import Request, Response, Usage

Transport = Callable[[Request], Response]

META_BASE_URL = "https://api.meta.ai"

# Environment variable holding each provider's key.
KEY_VARS = {"meta": "MODEL_API_KEY", "glimmer": "MUSE_API_KEY"}

POLICY_MARKERS = ("content management policy", "policy violation", "content policy", "safety policy")


def is_policy_refusal(status: int, message: str) -> bool:
    """A refusal under the provider's content policy (Meta: HTTP 400 "...triggering our content management policy",
    HTTP 403 "...restricted due to repeated policy violations"), as opposed to a bad request or a network failure."""
    text = message.casefold()
    return status in (400, 403) and any(m in text for m in POLICY_MARKERS)


def api_key_for(provider: str, config: Config) -> str | None:
    return {"meta": config.meta_api_key, "glimmer": config.glimmer_api_key}.get(provider)


class MessagesTransport:
    """Calls a Messages-compatible endpoint. The SDK client is built on first use."""

    def __init__(
        self,
        provider: str,
        client_kwargs: dict[str, Any],
        extra_params: Callable[[Request], dict[str, Any]] | None = None,
    ) -> None:
        self.provider = provider
        self._client_kwargs = client_kwargs
        self._extra_params = extra_params
        self._client: anthropic.Anthropic | None = None

    def __call__(self, request: Request) -> Response:
        if self._client is None:
            self._client = anthropic.Anthropic(**self._client_kwargs)
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": request.messages,
        }
        if request.system is not None:
            params["system"] = request.system
        if request.tools:
            params["tools"] = request.tools
        if request.temperature is not None:
            params["extra_body"] = {"temperature": request.temperature}
        if self._extra_params:
            params.update(self._extra_params(request))
        try:
            message = self._client.messages.create(**params)
        except anthropic.APIStatusError as e:
            if "credit balance" in str(e.message).casefold():
                raise BillingError(f"{self.provider}: HTTP {e.status_code}: {e.message}") from e
            if is_policy_refusal(e.status_code, str(e.message)):
                raise PolicyError(f"{self.provider}: HTTP {e.status_code}: {e.message}") from e
            raise ProviderError(f"{self.provider}: HTTP {e.status_code}: {e.message}") from e
        except anthropic.APIError as e:
            raise ProviderError(f"{self.provider}: {type(e).__name__}: {e}") from e
        return _normalize(message)


def _normalize(message: Any) -> Response:
    content: list[dict[str, Any]] = []
    ignored: list[str] = []
    for block in message.content:
        if block.type == "text":
            content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)})
        else:
            ignored.append(block.type)
    return Response(
        model=message.model,
        stop_reason=message.stop_reason,
        content=content,
        usage=Usage(message.usage.input_tokens or 0, message.usage.output_tokens or 0),
        ignored_block_types=tuple(ignored),
    )


def _glimmer_extra(request: Request) -> dict[str, Any]:
    # The model card's sampling: no greedy decoding for free-form generation. The SDK no longer takes
    # sampling arguments, so they go in the body directly.
    temperature = 1.0 if request.temperature is None else request.temperature
    return {"extra_body": {"temperature": temperature, "top_p": 0.95, "top_k": 64}}


def make_transport(provider: str, config: Config, **client_kwargs: Any) -> Transport:
    """Build the live transport for `provider`. Extra kwargs go to `anthropic.Anthropic`."""
    if provider == "meta":
        # Meta documents bearer auth with MODEL_API_KEY; `auth_token` sends `Authorization: Bearer`.
        # SDK 2.x sends `api_key` as a bearer token too, so if Meta expects `x-api-key`,
        # pass it via `default_headers={"x-api-key": ...}` here instead.
        return MessagesTransport(
            "meta", {"base_url": META_BASE_URL, "auth_token": config.meta_api_key, **client_kwargs}
        )
    if provider == "glimmer":
        # vLLM serves the Messages API next to the OpenAI one; the SDK appends `/v1/messages` itself. It thinks
        # before every answer (~25 tokens/s), so turns take minutes; thinking blocks are dropped by `_normalize`.
        return MessagesTransport(
            "glimmer",
            {
                "base_url": config.glimmer_base_url.removesuffix("/").removesuffix("/v1"),
                "auth_token": config.glimmer_api_key,
                "timeout": 900.0,
                **client_kwargs,
            },
            _glimmer_extra,
        )
    raise ValueError(f"unknown provider {provider!r}")
