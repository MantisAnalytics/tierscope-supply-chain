"""The HTTP API: a FastAPI app over the same services as the command line. Local, single process."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from scgraph.config import Config
from scgraph.editing.evidence import EvidenceStore
from scgraph.graph.store import GraphStore
from scgraph.ingest.pipeline import DEFAULT_SPECS_DIR
from scgraph.jobs import JobRunner, JobStore
from scgraph.llm.providers import Transport, api_key_for
from scgraph.ops.service import OpsService
from scgraph.ops.store import OpsStore

STATIC_DIR = Path(__file__).parent / "static"  # the built web app (ui/, `npm run build`)
NO_BUILD = """<!doctype html><meta charset="utf-8"><title>Tierscope</title>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; line-height: 1.5">
<h1>Tierscope: the web app is not built</h1>
<p>The API runs at <a href="/docs">/docs</a>. To build the app (needs Node 20+):</p>
<pre>cd ui && npm install && npm run build</pre>
<p>then reload this page.</p></body>"""


@dataclass
class AppState:
    config: Config
    store: GraphStore
    evidence: EvidenceStore
    specs_dir: Path
    transport: Transport | None
    lock: threading.Lock
    jobs: JobRunner
    ops: OpsService | None = None


def create_app(config: Config | None = None, *, specs_dir: Path = DEFAULT_SPECS_DIR,
               transport: Transport | None = None, static_dir: Path = STATIC_DIR) -> FastAPI:
    from scgraph.api.routes import agent, editing, graph, ingest, ops, overview, review, specs

    config = config or Config.from_env()
    app = FastAPI(title="scgraph", description="Supply chain graph: review, editing, exposure.")
    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins), allow_methods=["*"],
                       allow_headers=["*"])
    # 4 workers, so agent turns do not hold up batch ingest
    app.state.scgraph = AppState(config, config.graph_store(), EvidenceStore(config.evidence_dir), Path(specs_dir),
                                 transport, threading.Lock(), JobRunner(JobStore(config.jobs_db_path), workers=4))
    current = app.state.scgraph
    current.ops = OpsService(OpsStore(config.ops_db_path), current.store, _client_factory(config, transport),
                             current.lock, config=config, submit=current.jobs.submit)
    for module in (graph, overview, review, editing, ingest, specs, ops, agent):
        app.include_router(module.router, prefix="/api")
    _serve_app(app, Path(static_dir))
    return app


def _client_factory(config: Config, transport: Transport | None):
    """A model client for operations when the configured provider has a key (or a test transport is set)."""
    from scgraph.llm import LLMClient

    def make() -> LLMClient | None:
        if transport is None and not api_key_for(config.provider, config):
            return None
        return LLMClient(config, transport=transport)

    return make


def _serve_app(app: FastAPI, static: Path) -> None:
    """The built app at `/`: its files where they exist, else its entry page, so client-side routes load."""
    root = static.resolve()

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def web_app(path: str) -> FileResponse | HTMLResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, f"no API route /{path}")
        index = root / "index.html"
        if not index.is_file():
            return HTMLResponse(NO_BUILD)
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            immutable = file.parent == root / "assets"  # names carry a content hash
            return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable"
                                               if immutable else "no-cache"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def state(request: Request) -> Iterator[AppState]:
    """The app's state, one request at a time (one SQLite connection, single writer)."""
    current: AppState = request.app.state.scgraph
    with current.lock:
        yield current


def app_state(request: Request) -> AppState:
    """The app's state without the lock, for routes that only start or read background jobs."""
    return request.app.state.scgraph


def author(request: Request, x_author: str | None = Header(default=None)) -> str:
    """Who makes a change: the `X-Author` header, else the configured default."""
    return x_author or request.app.state.scgraph.config.author
