"""End-to-end table ingest: reuse or generate specs, validate, parse text fields, execute, load, report."""

from __future__ import annotations

import json
import re
import uuid
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scgraph.graph.facts import Fact, SourceLocation
from scgraph.graph.rows import LoadReport
from scgraph.graph.store import GraphStore
from scgraph.ingest.classify import FACT_KINDS, RegionPlan, classify
from scgraph.ingest.layout import StoredSpec, find_reusable, ignore_layout, load_stored
from scgraph.ingest.mapgen import Mapper
from scgraph.ingest.profile import Profiler
from scgraph.ingest.step import StepError
from scgraph.ingest.summary import exclude_summary_rows
from scgraph.ingest.textfields import (Cell, ColumnReport, extract_leftovers, generate_parser, parse_column,
                                       parse_parser)
from scgraph.ingest.validate import Known, graph_ids, validate_and_repair
from scgraph.llm import LLMClient, Response
from scgraph.mapping.execute import ExecutionResult, execute
from scgraph.mapping.spec import parse_spec
from scgraph.mapping.tables import TableFile, is_blank

DEFAULT_SPECS_DIR = Path("mappings/generated")


class IngestError(RuntimeError):
    pass


@dataclass
class RegionReport:
    name: str
    sheet: str | None
    kind: str
    source: str  # reused | generated
    path: Path | None
    spec: dict[str, Any]
    problems: list[str] = field(default_factory=list)
    repair_rounds: int = 0

    @property
    def excluded_rows(self) -> list[dict[str, Any]]:
        """Summary rows (TOTAL, ...) cut off under the region."""
        return self.spec.get("excluded_rows", [])


@dataclass
class IngestReport:
    file: str
    run_id: str
    regions: list[RegionReport] = field(default_factory=list)
    ignored: dict[str, str] = field(default_factory=dict)  # sheet -> reason
    text_fields: list[ColumnReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    results: dict[str, ExecutionResult] = field(default_factory=dict)
    load: LoadReport | None = None
    responses: list[Response] = field(default_factory=list)
    encoding: str | None = None  # csv: the text encoding read

    def llm_totals(self) -> dict[str, Any]:
        live = [r for r in self.responses if not r.cache_hit]
        return {
            "calls": len(self.responses),
            "cache_hits": len(self.responses) - len(live),
            "input_tokens": sum(r.usage.input_tokens for r in self.responses),
            "output_tokens": sum(r.usage.output_tokens for r in self.responses),
            "cost_usd": sum(r.cost_usd for r in self.responses),
        }


def _slug(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "csv").casefold()).strip("-")


def resolve_specs(profiler: Profiler, stored: list[StoredSpec]) -> tuple[dict[str | None, list[StoredSpec]], list]:
    """Stored specs per sheet that can be reused, and the sheets that need the model."""
    reused: dict[str | None, list[StoredSpec]] = {}
    unmatched = []
    for sheet in profiler.table.sheet_names:
        found = find_reusable(stored, profiler, sheet)
        if found is None:
            unmatched.append(sheet)
        else:
            reused[sheet] = found
    return reused, unmatched


def ingest(source: str | Path | TableFile, client: LLMClient | None, *, store: GraphStore | None = None,
           specs_dir: Path = DEFAULT_SPECS_DIR, reuse_only: bool = False, run_id: str | None = None,
           lock: AbstractContextManager | None = None, batch_values: set[str] | None = None) -> IngestReport:
    """Ingest a table file, or an in-memory table such as one read from a PDF.

    References in generated specs count as resolved when the graph (`store`) or another file of the batch
    (`batch_values`, its cell texts) names the id. `lock` is held around every access to `store`, so a caller
    sharing the store with other threads can run the model calls outside it.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    guard = lock or nullcontext()
    profiler = Profiler(source)
    path = profiler.path
    name = profiler.table.name
    report = IngestReport(file=name if profiler.table.label is None else f"{name} {profiler.table.label}",
                          run_id=run_id, encoding=profiler.table.encoding)
    reused, unmatched = resolve_specs(profiler, load_stored(specs_dir))
    if unmatched and (reuse_only or client is None):
        names = ", ".join(str(s) for s in unmatched)
        raise IngestError(f"no stored spec in {specs_dir} matches sheet(s): {names}")

    specs: dict[str, dict[str, Any]] = {}
    for sheet, stored_specs in reused.items():
        for s in stored_specs:
            if s.layout.get("kind") == "ignore":
                report.ignored.update(s.data.get("ignore_sheets", {}))
                continue
            spec_name = s.path.stem
            specs[spec_name] = exclude_summary_rows(s.data, profiler.table)
            report.regions.append(RegionReport(spec_name, sheet, s.data["metadata"].get("region_kind", "data"),
                                               "reused", s.path, specs[spec_name]))

    plans: dict[str, RegionPlan] = {}
    new_files: dict[str, dict[str, Any]] = {}
    if unmatched:
        step = classify(client, profiler, unmatched)
        report.responses += step.responses
        mapper = Mapper(client, profiler, step.value, run_id=run_id)
        stem = _slug(f"{path.stem} {profiler.table.label}") if profiler.table.label else path.stem
        for sheet in unmatched:
            sheet_plans = [p for p in step.value if p.sheet == sheet]
            if not any(p.kind in FACT_KINDS for p in sheet_plans):
                reason = "; ".join(f"{p.kind}: {p.reason}" for p in sheet_plans)
                report.ignored[sheet or path.stem] = reason
                if sheet is not None:
                    new_files[f"{stem}__ignore__{_slug(sheet)}"] = {
                        "version": 1, "file_kind": profiler.table.kind, "regions": [],
                        "metadata": {"author": "model", "model": client.default_model, "run_id": run_id,
                                     "status": "draft", "source_file": path.name, "region_kind": "ignore"},
                        "ignore_sheets": {sheet: reason}, "layout": ignore_layout(profiler, sheet),
                    }
        for plan in step.value:
            if plan.kind not in FACT_KINDS:
                continue
            try:
                generated = mapper.generate(plan)
            except StepError as e:
                report.errors.append(f"region {plan.id}: {e}")
                continue
            report.responses += generated.responses
            spec_name = f"{stem}__{plan.id}"
            plans[spec_name] = plan
            specs[spec_name] = generated.value
        known = Known(loose=set(batch_values or ()))
        if store is not None:
            with guard:
                known.typed = graph_ids(store)
        validated = validate_and_repair(mapper, plans, specs, profiler.table, known=known)
        report.responses += validated.responses
        for spec_name, plan in plans.items():
            specs[spec_name] = validated.specs[spec_name]
            report.regions.append(RegionReport(spec_name, plan.sheet, plan.kind, "generated",
                                               specs_dir / f"{spec_name}.json", specs[spec_name],
                                               validated.findings[spec_name].problems(),
                                               validated.repaired.get(spec_name, 0)))
            new_files[spec_name] = specs[spec_name]

    for spec_name, spec in specs.items():
        report.results[spec_name] = execute(parse_spec(spec), profiler.table, run_id=run_id,
                                            spec_name=str(specs_dir / f"{spec_name}.json"))
        report.facts += report.results[spec_name].facts

    for spec_name, spec in specs.items():
        for entry in spec.get("semi_structured", []):
            if spec["regions"][0]["columns"].get(entry["column"], {}).get("role") == "ignore":
                continue  # a person turned the column off
            facts = _text_column(client, profiler, spec_name, spec, entry, report, reuse_only=reuse_only,
                                 run_id=run_id, spec_name=str(specs_dir / f"{spec_name}.json"))
            report.facts += facts
            if spec_name in new_files:
                new_files[spec_name] = spec

    specs_dir.mkdir(parents=True, exist_ok=True)
    for spec_name, spec in new_files.items():
        (specs_dir / f"{spec_name}.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n")

    if store is not None:
        oracle = [o for r in report.results.values() for o in r.oracle]
        parameters = [p for r in report.results.values() for p in r.parameters]
        with guard:
            report.load = store.load(report.facts, inputs=[str(path) if name == path.name else name], oracle=oracle,
                                     parameters=parameters, run_id=run_id)
    return report


def _text_column(client: LLMClient | None, profiler: Profiler, name: str, spec: dict[str, Any],
                 entry: dict[str, Any], report: IngestReport, *, reuse_only: bool, run_id: str,
                 spec_name: str) -> list[Fact]:
    region_spec = spec["regions"][0]
    rows = region_spec.get("rows", {})
    region = profiler.table.region(region_spec["sheet"], header_row=region_spec.get("header_row"),
                                   start=rows.get("start", 2), end=rows.get("end"), stop_at_blank=rows.get("stop_at_blank"))
    column_report = ColumnReport(name, entry["column"])
    report.text_fields.append(column_report)
    letter = region.column(entry["column"])
    if letter is None:
        report.errors.append(f"{name}: semi-structured column {entry['column']!r} not found")
        return []
    result = report.results[name]
    origin = {"spec": spec_name, "region": region_spec["id"], "column": entry["column"]}
    cells = [
        Cell(region_spec["id"], row.number, str(row.cells.get(letter)).strip(),
             SourceLocation(file=region.file, sheet=region.sheet, row=row.number, column=letter),
             result.row_keys.get((region_spec["id"], row.number), {}), origin)
        for row in region.rows if not is_blank(row.cells.get(letter))
    ]
    if not cells:
        return []
    if "parser" not in entry:
        if reuse_only or client is None:
            report.errors.append(f"{name}: no stored parser for {entry['column']!r}")
            column_report.cells = len(cells)
            column_report.unparsed = [c.source.label() for c in cells]
            return []
        raw_target = region_spec["columns"].get(entry["column"], {}).get("target")
        entry["targets"] = [t for t in entry["targets"] if t != raw_target]  # the raw text stays where it is
        try:
            step = generate_parser(client, entry["column"], entry["targets"], [c.text for c in cells])
        except StepError as e:
            report.errors.append(f"{name}: {e}")
            return []
        report.responses += step.responses
        entry["parser"] = step.value.to_dict()
    parser = parse_parser(entry["parser"])
    fact_column = region_spec["columns"].get(entry["column"], {})
    facts, leftovers = parse_column(parser, cells, column_confidence=fact_column.get("confidence", 1.0),
                                    run_id=run_id, report=column_report)
    entry["coverage"] = round(column_report.coverage, 4)
    if leftovers:
        if reuse_only or client is None:
            column_report.unparsed = [c.source.label() for c in leftovers]
        else:
            try:
                extra, responses = extract_leftovers(client, leftovers, entry["targets"], run_id=run_id,
                                                     report=column_report)
                facts += extra
                report.responses += responses
            except StepError as e:
                report.errors.append(f"{name}: leftover extraction failed: {e}")
                column_report.unparsed = [c.source.label() for c in leftovers]
    return facts
