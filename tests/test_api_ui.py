"""The web app build served by the API, and the committed OpenAPI schema. TestClient only, no model calls."""

import json

from conftest import ROOT
from fakes import api_config as config


def test_openapi_export(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "openapi.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "export_openapi.py"), str(out)], check=True,
                   capture_output=True)
    schema = json.loads(out.read_text())
    from scgraph.api.routes import editing, graph, ingest, overview, review, specs

    routes = {"/api" + r.path.replace(":path", "") for m in (editing, graph, ingest, overview, review, specs)
              for r in m.router.routes}
    assert routes and routes <= set(schema["paths"])
    assert "depends_on" in schema["components"]["schemas"]["Impact"]["properties"]
    committed = json.loads((ROOT / "ui" / "src" / "api" / "openapi.json").read_text())
    assert committed == schema, "ui/src/api/openapi.json is stale: run `uv run python scripts/export_openapi.py`"


def test_deep_link(tmp_path):
    from fastapi.testclient import TestClient

    from scgraph.api.app import create_app

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Tierscope by Mantis Analytics</title>")
    (static / "assets" / "index-abc.js").write_text("console.log(1)")
    client = TestClient(create_app(config(tmp_path), static_dir=static))
    for path in ("/", "/explorer/Supplier/S100228", "/review/3"):
        r = client.get(path)
        assert r.status_code == 200 and "html" in r.headers["content-type"]
        assert "Tierscope by Mantis Analytics" in r.text
    asset = client.get("/assets/index-abc.js")
    assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
    assert client.get("/api/nope").status_code == 404 and client.get("/api/stats").status_code == 200
    assert "Tierscope by Mantis" in client.get("/../../etc/passwd").text  # never a file outside the build


def test_no_build(tmp_path):
    from fastapi.testclient import TestClient

    from scgraph.api.app import create_app

    client = TestClient(create_app(config(tmp_path), static_dir=tmp_path / "missing"))
    r = client.get("/explorer/Supplier/S100228")
    assert r.status_code == 200 and "npm run build" in r.text
    assert client.get("/api/stats").status_code == 200
