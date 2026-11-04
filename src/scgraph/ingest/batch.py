"""Batch ingest: many files and zip archives in one go, tables before documents, a report per file.

Archives are expanded into a temporary directory under fixed rules: entry names become safe relative paths,
hidden and system entries are ignored, archives inside archives are not opened, and a batch beyond
`MAX_FILES` files or `MAX_BYTES` uncompressed is refused before anything is ingested.
"""

from __future__ import annotations

import shutil
import uuid
import zipfile
import zlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from scgraph.graph.rows import LoadReport
from scgraph.graph.store import GraphStore
from scgraph.ingest.docingest import DocumentReport, ingest_document
from scgraph.ingest.pipeline import DEFAULT_SPECS_DIR, IngestReport, ingest
from scgraph.ingest.validate import cell_values
from scgraph.llm import LLMClient
from scgraph.mapping.tables import TableFile

TABLE_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".tsv")
DOCUMENT_SUFFIXES = (".pdf", ".txt", ".md")
ARCHIVE_SUFFIXES = (".zip",)
SUFFIXES = (*TABLE_SUFFIXES, *DOCUMENT_SUFFIXES, *ARCHIVE_SUFFIXES)
MAX_FILES = 200
MAX_BYTES = 200 * 1024 * 1024
CHUNK = 1024 * 1024
STATUSES = ("queued", "running", "done", "failed", "skipped")


class BatchError(ValueError):
    """The batch is refused as a whole (limits, unreadable archive); nothing was ingested."""


@dataclass
class BatchFile:
    name: str  # as reports name it: "bom.csv", or "exports.zip/folder/bom.csv" for an archive member
    kind: str  # table | document | skipped
    path: Path | None = None  # on disk; None when skipped
    status: str = "queued"
    reason: str | None = None  # why it was skipped
    error: str | None = None  # why it failed
    report: IngestReport | DocumentReport | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "kind": self.kind, "status": self.status}
        if self.reason:
            out["reason"] = self.reason
        if self.error:
            out["error"] = self.error
        if self.report is not None:
            out["report"] = report_dict(self.report)
        return out


@dataclass
class Batch:
    files: list[BatchFile] = field(default_factory=list)
    current: str | None = None  # the file being ingested

    def totals(self) -> dict[str, Any]:
        loads = [f.report.load for f in self.files if f.report is not None and f.report.load is not None]
        llm = [f.report.llm_totals() for f in self.files if f.report is not None]
        return {
            "files": len(self.files),
            **{s: sum(f.status == s for f in self.files) for s in STATUSES},
            "facts_applied": sum(load.applied for load in loads),
            "facts_proposed": sum(load.proposed for load in loads),
            "proposals_created": sum(len(load.proposals_created) for load in loads),
            "excluded_rows": sum(len(r.excluded_rows) for f in self.files if isinstance(f.report, IngestReport)
                                 for r in f.report.regions),
            "llm_calls": sum(t["calls"] for t in llm),
            "cost_usd": round(sum(t["cost_usd"] for t in llm), 6),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"files": [f.to_dict() for f in self.files], "current": self.current, "totals": self.totals()}


def kind_of(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in TABLE_SUFFIXES:
        return "table"
    if suffix in DOCUMENT_SUFFIXES:
        return "document"
    if suffix in ARCHIVE_SUFFIXES:
        return "archive"
    return None


def safe_name(entry: str) -> str | None:
    """A safe relative path for an archive entry, or None for entries to ignore (folders, hidden, system).

    An entry that climbs out (`..`) or is absolute keeps only its base name.
    """
    parts = entry.replace("\\", "/").split("/")
    if not parts[-1] or parts[0] == "__MACOSX" or any(p.startswith(".") and p not in (".", "..") for p in parts):
        return None
    escapes = entry.startswith(("/", "\\")) or ".." in parts or (len(parts[0]) == 2 and parts[0][1] == ":")
    kept = [p for p in parts if p not in ("", ".", "..")]
    if not kept or kept[-1].startswith("."):
        return None
    return kept[-1] if escapes else "/".join(kept)


def expand(inputs: list[tuple[str, Path]], workdir: Path, *, max_files: int | None = None,
           max_bytes: int | None = None) -> Batch:
    """The files of a batch: plain files as given, archives expanded into `workdir`; tables first, then documents.

    `inputs` are (name, path) pairs. Raises BatchError when the batch is beyond the limits or an archive is broken.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    root = workdir.resolve()
    found: list[BatchFile] = []
    limits = _Limits(max_files or MAX_FILES, max_bytes or MAX_BYTES)
    for name, path in inputs:
        kind = kind_of(name)
        if kind == "archive":
            found += _expand_zip(name, path, root, len(found), limits)
        elif kind is None:
            found.append(BatchFile(name, "skipped", status="skipped", reason="unsupported type"))
        else:
            limits.add(1, path.stat().st_size)
            found.append(BatchFile(name, kind, path))
    order = {"table": 0, "document": 1, "skipped": 2}
    return Batch(sorted(found, key=lambda f: order[f.kind]))  # stable: the given order within each kind


@dataclass
class _Limits:
    max_files: int
    max_bytes: int
    files: int = 0
    bytes: int = 0

    def add(self, files: int, size: int) -> None:
        self.files += files
        self.bytes += size
        if self.files > self.max_files:
            raise BatchError(f"the batch holds more than the limit of {self.max_files} files")
        if self.bytes > self.max_bytes:
            raise BatchError(f"the batch is larger than the limit of {_size(self.max_bytes)} uncompressed")


def _size(n: int) -> str:
    return f"{n // (1024 * 1024)} MB" if n >= 1024 * 1024 else f"{n} bytes"


def _expand_zip(name: str, path: Path, root: Path, offset: int, limits: _Limits) -> list[BatchFile]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BatchError(f"{name}: not a readable zip archive ({e})") from None
    out: list[BatchFile] = []
    with archive:
        entries = [(info, safe_name(info.filename)) for info in archive.infolist() if not info.is_dir()]
        entries = [(info, rel) for info, rel in entries if rel is not None]
        for info, rel in entries:
            if kind_of(rel) in ("table", "document"):
                limits.add(1, info.file_size)  # declared sizes first: refuse before writing anything
        for info, rel in entries:
            member = f"{name}/{rel}"
            kind = kind_of(rel)
            if kind == "archive":
                out.append(BatchFile(member, "skipped", status="skipped", reason="archive inside an archive"))
                continue
            if kind is None:
                out.append(BatchFile(member, "skipped", status="skipped", reason="unsupported type"))
                continue
            target = (root / f"{offset + len(out):03d}" / PurePosixPath(rel).name).resolve()
            if not target.is_relative_to(root):
                raise BatchError(f"{member}: unsafe name")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as src, target.open("wb") as dst:
                    read = 0
                    while chunk := src.read(CHUNK):
                        beyond = max(read - info.file_size, 0)
                        read += len(chunk)
                        limits.add(0, max(read - info.file_size, 0) - beyond)  # more than declared: actual bytes
                        dst.write(chunk)
            except (zipfile.BadZipFile, zlib.error, OSError, RuntimeError, NotImplementedError) as e:
                raise BatchError(f"{member}: cannot be read from the archive ({e})") from None
            out.append(BatchFile(member, kind, target))
    return out


def run_batch(batch: Batch, client: LLMClient | None, *, store: GraphStore | None,
              specs_dir: Path = DEFAULT_SPECS_DIR, reuse_only: bool = False,
              lock: AbstractContextManager | None = None,
              on_change: Callable[[Batch], None] | None = None,
              on_disruption: Callable[[str, str], dict[str, Any]] | None = None) -> Batch:
    """Ingest every file in order. A failing file is recorded and the rest continue.

    References count as resolved against the graph and the cell values of the batch's other table files;
    documents resolve mentions against the entities the batch's tables described. A document that reports a
    disruption is handed to `on_disruption(text, file name)`, which returns the draft disruption it created.
    """
    notify = on_change or (lambda _: None)
    tables: dict[str, TableFile] = {}
    for f in batch.files:
        if f.kind == "table" and f.path is not None:
            try:
                tables[f.name] = TableFile(f.path)
                tables[f.name].name = f.name
            except Exception as e:  # noqa: BLE001 - reported per file
                f.status, f.error = "failed", f"{type(e).__name__}: {e}"
    values = {name: cell_values(t) for name, t in tables.items()}
    table_facts = []
    notify(batch)
    for f in batch.files:
        if f.status != "queued":
            continue
        batch.current, f.status = f.name, "running"
        notify(batch)
        others = set().union(*(v for n, v in values.items() if n != f.name))
        run_id = uuid.uuid4().hex[:12]
        try:
            if f.kind == "table":
                f.report = ingest(tables[f.name], client, store=store, specs_dir=specs_dir, reuse_only=reuse_only,
                                  run_id=run_id, lock=lock, batch_values=others)
                table_facts += f.report.facts
            else:
                f.report = ingest_document(f.path, client, store=store, specs_dir=specs_dir, reuse_only=reuse_only,
                                           run_id=run_id, name=f.name, lock=lock, batch_values=others,
                                           context_facts=table_facts)
                if f.report.disruption_text is not None and on_disruption is not None:
                    try:
                        f.report.disruption = on_disruption(f.report.disruption_text, f.name)
                    except Exception as e:  # noqa: BLE001 - reported with the file
                        f.report.errors.append(f"draft disruption: {e}")
            f.status = "done"
        except Exception as e:  # noqa: BLE001 - one broken file must not stop the batch
            f.status, f.error = "failed", str(e) or type(e).__name__
        batch.current = None
        notify(batch)
    return batch


def remove(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


# -- reports as JSON (the API, jobs and the web app) ----------------------------

def load_dict(load: LoadReport | None) -> dict[str, Any]:
    if load is None:
        return {}
    return load.counts() | {"proposals_created": load.proposals_created, "proposals_updated": load.proposals_updated,
                            "journal_id": load.journal_id}


def table_dict(report: IngestReport) -> dict[str, Any]:
    return {"kind": "table", "file": report.file, "run_id": report.run_id, "encoding": report.encoding,
            "regions": [{"name": r.name, "sheet": r.sheet, "kind": r.kind, "source": r.source,
                         "problems": r.problems, "repair_rounds": r.repair_rounds,
                         "excluded_rows": r.excluded_rows} for r in report.regions],
            "ignored": report.ignored, "text_fields": [{"column": t.column, "coverage": t.coverage}
                                                       for t in report.text_fields],
            "errors": report.errors, "load": load_dict(report.load), "llm": report.llm_totals()}


def document_dict(report: DocumentReport) -> dict[str, Any]:
    return {"kind": "document", "file": report.file, "run_id": report.run_id,
            "tables": [table_dict(t) for t in report.tables], "chunks": report.chunks,
            "extracted": report.extracted, "rejected": report.rejected, "flagged": report.flagged,
            "unresolved": report.unresolved, "errors": report.errors, "load": load_dict(report.load),
            "disruption_report": report.disruption_text is not None, "disruption": report.disruption,
            "llm": report.llm_totals()}


def report_dict(report: IngestReport | DocumentReport) -> dict[str, Any]:
    return document_dict(report) if isinstance(report, DocumentReport) else table_dict(report)
