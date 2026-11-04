"""Batch ingest over the API: start a job, poll it, list jobs, refuse batches beyond the limits."""

import io
import threading
import time
import zipfile

import pytest
from fakes import FakeTransport, tool_response
from fakes import api_config as config
from fastapi.testclient import TestClient
from test_batch import COMPONENTS, SUPPLIERS, plan

from scgraph.api.app import create_app


def wait(client, job_id, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/ingest/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture
def make_client(tmp_path):
    def make(transport=None):
        cfg = config(tmp_path, SCGRAPH_LLM_CACHE="off", SCGRAPH_LLM_PROVIDER="meta", MODEL_API_KEY="k")
        return TestClient(create_app(cfg, specs_dir=tmp_path / "gen", transport=transport))
    return make


SUPPLIERS_CSV = b"Supplier ID,Name\nS1,Acme Inc.\nS2,Bolt AG\n"
COMPONENTS_CSV = b"Component ID,Supplier ID\nC1,S1\nC2,S2\n"


def test_start_and_poll_a_batch(make_client):
    transport = FakeTransport([
        tool_response("submit_regions", plan("components", "Component")),
        tool_response("submit_mapping", COMPONENTS),
        tool_response("submit_regions", plan("suppliers", "Supplier")),
        tool_response("submit_mapping", SUPPLIERS),
    ])
    with make_client(transport) as client:
        r = client.post("/api/ingest/batch", files=[("files", ("components.csv", COMPONENTS_CSV)),
                                                    ("files", ("suppliers.csv", SUPPLIERS_CSV))])
        assert r.status_code == 200, r.text
        started = r.json()
        assert started["status"] in ("queued", "running") and started["kind"] == "ingest"
        assert [f["name"] for f in started["progress"]["files"]] == ["components.csv", "suppliers.csv"]
        job = wait(client, started["id"])
        assert job["status"] == "done", job["error"]
        files = job["result"]["files"]
        assert [(f["name"], f["status"]) for f in files] == [("components.csv", "done"), ("suppliers.csv", "done")]
        assert files[0]["report"]["kind"] == "table" and files[0]["report"]["regions"][0]["repair_rounds"] == 0
        assert files[0]["report"]["encoding"] == "utf-8"
        totals = job["result"]["totals"]
        assert totals["done"] == 2 and totals["llm_calls"] == 4 and totals["facts_applied"] > 0
        assert client.get("/api/nodes/Supplier/S1").status_code == 200

        jobs = client.get("/api/ingest/jobs").json()
        assert [j["id"] for j in jobs] == [started["id"]] and jobs[0]["title"].startswith("2 files")
        assert client.get("/api/ingest/jobs/nope").status_code == 404


def test_the_graph_stays_usable_while_a_batch_waits_on_the_model(make_client):
    entered, release = threading.Event(), threading.Event()

    class SlowTransport(FakeTransport):
        def __call__(self, request):
            entered.set()
            release.wait(10)
            return super().__call__(request)

    transport = SlowTransport([tool_response("submit_regions", plan("suppliers", "Supplier")),
                               tool_response("submit_mapping", SUPPLIERS)])
    with make_client(transport) as client:
        job_id = client.post("/api/ingest/batch", files=[("files", ("suppliers.csv", SUPPLIERS_CSV))]).json()["id"]
        assert entered.wait(5)  # the job is waiting for the model now
        first = client.get(f"/api/ingest/jobs/{job_id}").json()
        assert first["status"] == "running" and first["progress"]["current"] == "suppliers.csv"
        t0 = time.monotonic()
        assert client.get("/api/stats").status_code == 200  # the graph lock is free meanwhile
        assert time.monotonic() - t0 < 5
        release.set()
        job = wait(client, job_id)
        done_first = sum(f["status"] == "done" for f in first["progress"]["files"])
        assert sum(f["status"] == "done" for f in job["result"]["files"]) >= done_first
        assert job["status"] == "done"


def test_zip_upload_with_junk(make_client):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("exports/suppliers.csv", SUPPLIERS_CSV)
        z.writestr("exports/photo.png", b"\x89PNG")
        z.writestr("__MACOSX/exports/._suppliers.csv", b"junk")
    transport = FakeTransport([tool_response("submit_regions", plan("suppliers", "Supplier")),
                               tool_response("submit_mapping", SUPPLIERS)])
    with make_client(transport) as client:
        r = client.post("/api/ingest/batch", files=[("files", ("exports.zip", archive.getvalue()))])
        job = wait(client, r.json()["id"])
        assert [(f["name"], f["status"], f.get("reason")) for f in job["result"]["files"]] == [
            ("exports.zip/exports/suppliers.csv", "done", None),
            ("exports.zip/exports/photo.png", "skipped", "unsupported type")]


def test_batch_over_the_limit_answers_422(make_client, monkeypatch):
    import scgraph.ingest.batch as batch

    monkeypatch.setattr(batch, "MAX_BYTES", 1000)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.csv", b"id\n" + b"x\n" * 5000)  # compresses well, expands beyond the limit
    with make_client(FakeTransport()) as client:
        r = client.post("/api/ingest/batch", files=[("files", ("big.zip", archive.getvalue()))])
        assert r.status_code == 422 and "limit of 1000 bytes" in r.json()["detail"]
        assert client.get("/api/ingest/jobs").json() == []


def test_nothing_to_ingest_answers_422(make_client):
    with make_client() as client:
        r = client.post("/api/ingest/batch", files=[("files", ("photo.png", b"\x89PNG"))])
        assert r.status_code == 422 and client.get("/api/ingest/jobs").json() == []
