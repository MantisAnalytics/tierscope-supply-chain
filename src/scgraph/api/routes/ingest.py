"""Ingest uploaded files through the review gate: one file at once, or a batch of files and zip archives as a job."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from scgraph.api.app import AppState, app_state, author, state
from scgraph.api.models import IngestReport, Open
from scgraph.ingest.batch import (DOCUMENT_SUFFIXES, SUFFIXES, TABLE_SUFFIXES, Batch, BatchError, document_dict,
                                  expand, remove, run_batch, table_dict)
from scgraph.ingest.docingest import ingest_document
from scgraph.ingest.pipeline import IngestError, ingest
from scgraph.ingest.step import StepError
from scgraph.jobs import Job
from scgraph.llm import LLMClient, LLMError
from scgraph.mapping.spec import SpecError
from scgraph.mapping.tables import TableError

router = APIRouter(tags=["ingest"])
FILE_SUFFIXES = (*TABLE_SUFFIXES, *DOCUMENT_SUFFIXES)
JOB_KIND = "ingest"


class BatchFileOut(Open):
    name: str
    kind: str  # table, document or skipped
    status: str  # queued, running, done, failed, skipped
    reason: str | None = None
    error: str | None = None
    report: IngestReport | None = None


class BatchState(Open):
    files: list[BatchFileOut]
    current: str | None = None
    totals: dict[str, Any]


class IngestJob(Open):
    id: str
    kind: str
    status: str  # queued, running, done, failed
    title: str
    created_at: str
    updated_at: str
    progress: BatchState
    result: BatchState | None = None
    error: str | None = None


@router.post("/ingest", response_model=IngestReport)
async def ingest_file(file: UploadFile = File(...), reuse_only: bool = Form(False),
                      st: AppState = Depends(state), who: str = Depends(author)) -> dict[str, Any]:
    """Table files reuse or generate mapping specs; documents go through text extraction, except a document that
    reports a disruption, which becomes a draft disruption (named in the report) for the agent to plan. Model calls
    follow the configured provider, cache mode and budget; `reuse_only` makes none (stored specs and parsers
    only)."""
    name = Path(file.filename or "").name
    if Path(name).suffix.lower() not in FILE_SUFFIXES:
        raise HTTPException(422, f"upload one of {', '.join(FILE_SUFFIXES)}, got {name!r}")
    data = await file.read()
    client = None if reuse_only else LLMClient(st.config, transport=st.transport)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        path.write_bytes(data)
        try:
            if path.suffix.lower() in DOCUMENT_SUFFIXES:
                report = ingest_document(path, client, store=st.store, specs_dir=st.specs_dir, reuse_only=reuse_only)
                if report.disruption_text is not None:
                    report.disruption = st.ops.draft_from_document(report.disruption_text, name, who, locked=True)
                return document_dict(report)
            report = ingest(path, client, store=st.store, specs_dir=st.specs_dir, reuse_only=reuse_only)
            return table_dict(report)
        except (IngestError, StepError, LLMError, SpecError, TableError, ValueError) as e:
            raise HTTPException(422, f"ingest failed: {e}") from None


@router.post("/ingest/batch", response_model=IngestJob)
async def ingest_batch(files: list[UploadFile] = File(...), reuse_only: bool = Form(False),
                       st: AppState = Depends(app_state), who: str = Depends(author)) -> dict[str, Any]:
    """Start a batch job over one or more files and zip archives; answers at once with the job to poll.

    Tables are ingested before documents; each file gets its own report. A batch beyond the limits (200 files,
    200 MB uncompressed) is refused with 422 and no job is created."""
    workdir = Path(tempfile.mkdtemp(prefix="scgraph-batch-"))
    try:
        inputs = []
        for i, upload in enumerate(files):
            name = Path(upload.filename or f"file{i}").name
            path = workdir / "uploads" / f"{i:03d}" / name
            path.parent.mkdir(parents=True)
            path.write_bytes(await upload.read())
            inputs.append((name, path))
        batch = expand(inputs, workdir / "expanded")
    except BatchError as e:
        remove(workdir)
        raise HTTPException(422, str(e)) from None
    except BaseException:
        remove(workdir)
        raise
    if not any(f.kind != "skipped" for f in batch.files):
        remove(workdir)
        raise HTTPException(422, f"no file to ingest; upload {', '.join(SUFFIXES)}")

    def work(job: Job) -> dict[str, Any]:
        try:
            client = None if reuse_only else LLMClient(st.config, transport=st.transport)
            run_batch(batch, client, store=st.store, specs_dir=st.specs_dir, reuse_only=reuse_only, lock=st.lock,
                      on_change=lambda b: job.report(**b.to_dict()),
                      on_disruption=lambda text, name: st.ops.draft_from_document(text, name, who))
            return batch.to_dict()
        finally:
            remove(workdir)

    job_id = st.jobs.submit(JOB_KIND, _title(batch), work, batch.to_dict())
    return st.jobs.store.get(job_id)


@router.get("/ingest/jobs", response_model=list[IngestJob])
def ingest_jobs(limit: int = Query(20, ge=1, le=100), st: AppState = Depends(app_state)) -> list[dict[str, Any]]:
    """Recent batch jobs, newest first."""
    return st.jobs.store.list(JOB_KIND, limit)


@router.get("/ingest/jobs/{job_id}", response_model=IngestJob)
def ingest_job(job_id: str, st: AppState = Depends(app_state)) -> dict[str, Any]:
    """A batch job: status, each file's status and, once ingested, its report, and the totals."""
    job = st.jobs.store.get(job_id)
    if job is None or job["kind"] != JOB_KIND:
        raise HTTPException(404, f"no ingest job {job_id}")
    return job


def _title(batch: Batch) -> str:
    names = [f.name for f in batch.files if f.kind != "skipped"]
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    return f"{len(names)} file{'s' if len(names) != 1 else ''}: {shown}"
