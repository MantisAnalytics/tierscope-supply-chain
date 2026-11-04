"""Normalized request and response types, and validation of the portable subset.

Messages, content blocks and tools are plain dicts in Messages API shape,
restricted to what both Muse providers (Spark and Glimmer) are expected to support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scgraph.llm.errors import UnsupportedFeatureError

ROLES = ("user", "assistant")
BLOCK_KEYS = {
    "text": {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content", "is_error"},
}
TOOL_KEYS = {"name", "description", "input_schema"}


@dataclass(frozen=True)
class Request:
    messages: list[dict[str, Any]]
    system: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 4096
    # Left out of the provider request when None.
    temperature: float | None = None
    # Per-request override of the configured model.
    model: str | None = None
    # Run seed: part of the cache key only, so repeated runs (benchmarks) are not served one cached answer. Never
    # sent to a provider; left out of the key when None, so keys from before seeds existed stay valid.
    seed: int | None = None

    def validate(self) -> None:
        if self.system is not None and not isinstance(self.system, str):
            raise UnsupportedFeatureError("system must be a plain string (system blocks are not portable)")
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise UnsupportedFeatureError("max_tokens must be a positive integer")
        if self.seed is not None and (not isinstance(self.seed, int) or isinstance(self.seed, bool)):
            raise UnsupportedFeatureError("seed must be an integer")
        if not self.messages:
            raise UnsupportedFeatureError("messages must not be empty")
        for i, message in enumerate(self.messages):
            _validate_message(i, message)
        for tool in self.tools:
            _validate_tool(tool)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "model": self.model,
            "system": self.system,
            "messages": self.messages,
            "tools": self.tools,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if self.seed is not None:
            out["seed"] = self.seed
        return out


def _validate_message(index: int, message: dict[str, Any]) -> None:
    where = f"messages[{index}]"
    extra = set(message) - {"role", "content"}
    if extra:
        raise UnsupportedFeatureError(f"{where}: unsupported field(s) {sorted(extra)}")
    if message.get("role") not in ROLES:
        raise UnsupportedFeatureError(f"{where}: role must be one of {ROLES}, got {message.get('role')!r}")
    content = message.get("content")
    if isinstance(content, str):
        return
    if not isinstance(content, list):
        raise UnsupportedFeatureError(f"{where}: content must be a string or a list of blocks")
    for j, block in enumerate(content):
        _validate_block(f"{where}.content[{j}]", block)


def _validate_block(where: str, block: dict[str, Any]) -> None:
    block_type = block.get("type") if isinstance(block, dict) else None
    allowed = BLOCK_KEYS.get(block_type)
    if allowed is None:
        raise UnsupportedFeatureError(
            f"{where}: block type {block_type!r} is not in the portable subset ({sorted(BLOCK_KEYS)})"
        )
    extra = set(block) - allowed
    if extra:
        raise UnsupportedFeatureError(f"{where}: {block_type} block has unsupported field(s) {sorted(extra)}")
    if block_type == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            for k, inner in enumerate(content):
                if not (isinstance(inner, dict) and inner.get("type") == "text"):
                    raise UnsupportedFeatureError(f"{where}.content[{k}]: tool_result content must be text")
                _validate_block(f"{where}.content[{k}]", inner)
        elif not isinstance(content, str):
            raise UnsupportedFeatureError(f"{where}: tool_result content must be a string or text blocks")


def _validate_tool(tool: dict[str, Any]) -> None:
    name = tool.get("name")
    if "type" in tool:
        raise UnsupportedFeatureError(
            f"tool {name!r}: provider-hosted tool type {tool['type']!r} is not supported; "
            "only client-side tools with name, description and input_schema"
        )
    extra = set(tool) - TOOL_KEYS
    if extra:
        raise UnsupportedFeatureError(f"tool {name!r}: unsupported field(s) {sorted(extra)}")
    if not isinstance(name, str) or not name:
        raise UnsupportedFeatureError("tool definition needs a non-empty name")
    schema = tool.get("input_schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise UnsupportedFeatureError(f"tool {name!r}: input_schema must be a JSON Schema object")


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Response:
    model: str
    stop_reason: str | None
    # Portable blocks only: `text` and `tool_use`.
    content: list[dict[str, Any]]
    usage: Usage = Usage()
    # Block types the provider returned that are outside the subset and were dropped.
    ignored_block_types: tuple[str, ...] = ()
    # Set by the client, not persisted in the cache.
    cost_usd: float = 0.0
    cache_hit: bool = False

    @property
    def text(self) -> str:
        return "".join(b["text"] for b in self.content if b["type"] == "text")

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [ToolCall(b["id"], b["name"], b["input"]) for b in self.content if b["type"] == "tool_use"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stop_reason": self.stop_reason,
            "content": self.content,
            "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
            "ignored_block_types": list(self.ignored_block_types),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Response:
        return cls(
            model=data["model"],
            stop_reason=data["stop_reason"],
            content=data["content"],
            usage=Usage(**data["usage"]),
            ignored_block_types=tuple(data.get("ignored_block_types", ())),
        )
