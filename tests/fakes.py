"""Scripted fake transport and helpers for tests."""

from __future__ import annotations

from collections.abc import Iterable

from scgraph.config import Config
from scgraph.llm.types import Request, Response, Usage


class FakeTransport:
    """Returns scripted responses in order and records every request it receives."""

    def __init__(self, responses: Iterable[Response] = ()) -> None:
        self.responses = list(responses)
        self.requests: list[Request] = []

    def __call__(self, request: Request) -> Response:
        self.requests.append(request)
        if not self.responses:
            return text_response("ok", model=request.model)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def calls(self) -> int:
        return len(self.requests)


def text_response(text: str, *, model: str = "muse-spark-1.1", usage: Usage = Usage(100, 20)) -> Response:
    return Response(model=model, stop_reason="end_turn", content=[{"type": "text", "text": text}], usage=usage)


def tool_response(
    name: str, input: dict, *, id: str = "toolu_1", model: str = "muse-spark-1.1", usage: Usage = Usage(100, 20)
) -> Response:
    return Response(
        model=model,
        stop_reason="tool_use",
        content=[{"type": "tool_use", "id": id, "name": name, "input": input}],
        usage=usage,
    )


def api_config(tmp, **env) -> Config:
    """Config for an API test app: every database and the evidence dir under `tmp`, cache in replay."""
    return Config.from_env({"SCGRAPH_GRAPH_DB": str(tmp / "graph.sqlite"), "SCGRAPH_EVIDENCE_DIR": str(tmp / "ev"),
                            "SCGRAPH_LLM_DB": str(tmp / "llm.sqlite"), "SCGRAPH_LLM_CACHE": "replay",
                            "SCGRAPH_AUTHOR": "default-author", "SCGRAPH_JOBS_DB": str(tmp / "jobs.sqlite"),
                            "SCGRAPH_OPS_DB": str(tmp / "ops.sqlite"), **env})


def make_config(tmp_path, **env) -> Config:
    base = {
        # Pinned so the ledger and budget tests price calls at Muse Spark rates whatever the environment says.
        "SCGRAPH_LLM_PROVIDER": "meta",
        "MODEL_API_KEY": "test-meta-key",
        "MUSE_API_KEY": "test-muse-key",
        "SCGRAPH_LLM_DB": str(tmp_path / "llm.sqlite"),
    }
    base.update(env)
    return Config.from_env({k: v for k, v in base.items() if v is not None})


def user(text: str) -> Request:
    return Request(messages=[{"role": "user", "content": text}])
