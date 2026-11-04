"""Dry run of generated specs, findings, repair rounds and evidence-based column confidence."""

from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from scgraph.graph.schema import SCHEMA, key_id
from scgraph.ingest.classify import RegionPlan
from scgraph.ingest.mapgen import Mapper
from scgraph.ingest.step import StepError
from scgraph.ingest.summary import exclude_summary_rows
from scgraph.llm import Response
from scgraph.mapping.execute import ExecutionResult, execute
from scgraph.mapping.spec import parse_spec
from scgraph.mapping.tables import TableFile, is_blank

if TYPE_CHECKING:
    from scgraph.graph.store import GraphStore

MAX_FAILURE_SHARE = 0.05
MAX_UNRESOLVED_SHARE = 0.05
DUPLICATE_KEY_FACTOR = 0.8
REPAIR_ROUNDS = 2
SAMPLES = 5


def column_confidence(model: float, success_share: float = 1.0, *, duplicates: bool = False,
                      resolved_share: float | None = None) -> float:
    """Model confidence capped by evidence: transform success, duplicate keys, unresolved references."""
    value = min(model, success_share)
    if duplicates:
        value *= DUPLICATE_KEY_FACTOR
    if resolved_share is not None:
        value *= resolved_share
    return round(max(0.0, min(1.0, value)), 4)


@dataclass
class Share:
    bad: int = 0
    total: int = 0
    samples: list[Any] = field(default_factory=list)

    @property
    def share(self) -> float:
        return self.bad / self.total if self.total else 0.0


@dataclass
class Findings:
    region: str
    failures: dict[str, Share] = field(default_factory=dict)  # column header -> transform failures
    duplicate_keys: list[str] = field(default_factory=list)
    list_keys: list[str] = field(default_factory=list)  # keys that are lists of other regions' keys
    references: dict[str, Share] = field(default_factory=dict)  # column header(s) -> unresolved ids
    unmapped: list[str] = field(default_factory=list)

    def problems(self) -> list[str]:
        out = [f"column {h!r}: {s.bad} of {s.total} cells fail their transform, e.g. {s.samples}"
               for h, s in self.failures.items() if s.share > MAX_FAILURE_SHARE]
        if self.duplicate_keys:
            out.append(f"{len(self.duplicate_keys)} duplicate keys, e.g. {self.duplicate_keys[:SAMPLES]}")
        if self.list_keys:
            out.append(f"{len(self.list_keys)} keys are lists of ids that other regions already describe, e.g. "
                       f"{self.list_keys[:SAMPLES]}: the column references those entities, it does not describe them")
        out += [f"reference column {h!r}: {s.bad} of {s.total} ids do not resolve to any region, e.g. {s.samples}"
                for h, s in self.references.items() if s.share > MAX_UNRESOLVED_SHARE]
        out += [f"column {h!r} is not mapped" for h in self.unmapped]
        return out

    def score(self) -> int:
        return (sum(s.bad for s in self.failures.values()) + len(self.duplicate_keys) + 100 * len(self.list_keys)
                + sum(s.bad for s in self.references.values()) + 100 * len(self.unmapped))

    def to_dict(self) -> dict[str, Any]:
        return {
            "problems": self.problems(),
            "failure_share": {h: round(s.share, 4) for h, s in self.failures.items()},
            "duplicate_keys": len(self.duplicate_keys),
            "unresolved_share": {h: round(s.share, 4) for h, s in self.references.items()},
        }


@dataclass
class Known:
    """Ids a reference may name besides the keys of the file's own regions."""

    typed: dict[str, set[str]] = field(default_factory=dict)  # entity type -> ids in the graph
    loose: set[str] = field(default_factory=set)  # cell values of the other files of a batch, of any type

    def has(self, type_name: str, value: str) -> bool:
        return value in self.typed.get(type_name, ()) or value in self.loose


def graph_ids(store: GraphStore) -> dict[str, set[str]]:
    """Entity ids in the graph by type: accepted nodes and nodes a pending proposal would add."""
    ids: dict[str, set[str]] = defaultdict(set)
    for type_name, node_id, _ in store.nodes():
        ids[type_name].add(node_id)
    for type_name, node_id in store.proposed_node_ids():
        ids[type_name].add(node_id)
    return dict(ids)


def cell_values(table: TableFile) -> set[str]:
    """Every non-blank cell of a table as text, for loose cross-file reference checks."""
    out: set[str] = set()
    for sheet in table.sheet_names:
        for row in table._grid(sheet):
            for value in row:
                if is_blank(value) or isinstance(value, bool):
                    continue
                if isinstance(value, float) and value.is_integer():
                    out.add(str(int(value)))
                out.add(str(value).strip())
    return out


def dry_run(specs: dict[str, dict[str, Any]], table: TableFile,
            known: Known | None = None) -> tuple[dict[str, ExecutionResult], dict[str, Findings]]:
    """Execute every spec without loading; findings need all regions for cross-region references.

    A reference resolves when a region of the file has the id as a key, or `known` (the graph, the batch) has it.
    """
    results = {rid: execute(parse_spec(spec), table) for rid, spec in specs.items()}
    ids: dict[str, set[str]] = defaultdict(set)
    for result in results.values():
        for f in result.facts:
            if f.kind == "entity":
                ids[f.type].add(key_id(f.key))
    findings = {}
    for rid, spec in specs.items():
        findings[rid] = _findings(rid, spec, results[rid], table, ids, known or Known())
    return results, findings


def _findings(rid: str, spec: dict[str, Any], result: ExecutionResult, table: TableFile,
              ids: dict[str, set[str]], known: Known) -> Findings:
    found = Findings(rid)
    if not spec["regions"]:
        return found
    region_spec = spec["regions"][0]
    rows = region_spec.get("rows", {})
    region = table.region(region_spec["sheet"], header_row=region_spec.get("header_row"), start=rows.get("start", 2),
                          end=rows.get("end"), stop_at_blank=rows.get("stop_at_blank"))
    failed = Counter(f.column for f in result.failures)
    samples: dict[str, list] = defaultdict(list)
    for f in result.failures:
        if len(samples[f.column]) < SAMPLES:
            samples[f.column].append(f.raw)
    for header, column in region_spec["columns"].items():
        if column["role"] in ("fact", "derived"):
            letter = region.column(header)
            cells = sum(not is_blank(r.cells.get(letter)) for r in region.rows)
            found.failures[header] = Share(failed[header], cells, samples[header])
    describes = spec.get("metadata", {}).get("describes")
    entity = next((p for p in region_spec["produces"] if p.get("entity") == describes), None)
    if entity:  # only the region's own entity must be unique; rows of a relation table repeat by design
        keys = Counter(key_id(f.key) for f in result.facts if f.kind == "entity" and f.type == entity["entity"])
        found.duplicate_keys = sorted(k for k, n in keys.items() if n > 1)
        found.list_keys = sorted(k for k in keys if len(parts := re.split(r"\s*[,;]\s*", k)) > 1
                                 and all(p in ids[entity["entity"]] and p != k for p in parts))
    for produce in region_spec["produces"]:
        if "relation" not in produce:
            continue
        rel = SCHEMA.relation(produce["relation"])
        checks = [(_label(produce["from"]), rel.src, 0), (_label(produce["to"]), rel.dst, 1)]
        for i, fdef in enumerate(rel.identity, start=2):
            if fdef.ref and fdef.name in produce.get("identity", {}):
                checks.append((_label(produce["identity"][fdef.name]), fdef.ref, i))
        for label, type_name, index in checks:
            if label is None:  # constant
                continue
            share = found.references.setdefault(label, Share())
            for f in result.facts:
                if f.kind == "relation" and f.type == rel.name and f.key[index] is not None:
                    share.total += 1
                    if f.key[index] not in ids[type_name] and not known.has(type_name, f.key[index]):
                        share.bad += 1
                        if len(share.samples) < SAMPLES:
                            share.samples.append(f.key[index])
    found.unmapped = [u.header or u.column for u in result.unmapped]
    return found


def _label(ref: Any) -> str | None:
    refs = ref if isinstance(ref, list) else [ref]
    columns = [r["column"] for r in refs if "column" in r]
    return " + ".join(columns) if columns else None


def apply_confidence(spec: dict[str, Any], findings: Findings) -> dict[str, Any]:
    """Write evidence-capped confidences into the spec; the model's own value stays as `model_confidence`."""
    spec = copy.deepcopy(spec)
    if not spec["regions"]:
        return spec
    region = spec["regions"][0]
    describes = spec.get("metadata", {}).get("describes")
    entity = next((p for p in region["produces"] if p.get("entity") == describes), None)
    key_headers = set(_label(entity["key"]).split(" + ")) if entity else set()
    resolved: dict[str, float] = {}
    for label, share in findings.references.items():
        for header in label.split(" + "):
            if header not in key_headers and share.total:
                resolved[header] = min(resolved.get(header, 1.0), 1 - share.share)
    for header, column in (*region["columns"].items(), *region.get("key_columns", {}).items()):
        model = column.get("model_confidence", column.get("confidence", 1.0))
        failure = findings.failures.get(header)
        column["model_confidence"] = model
        column["confidence"] = column_confidence(
            model, 1 - failure.share if failure else 1.0,
            duplicates=header in key_headers and bool(findings.duplicate_keys),
            resolved_share=resolved.get(header),
        )
    keys = [c["confidence"] for c in region.get("key_columns", {}).values()]
    region["confidence"] = min(keys) if keys else 1.0
    return spec


@dataclass
class Validated:
    specs: dict[str, dict[str, Any]]
    results: dict[str, ExecutionResult]
    findings: dict[str, Findings]
    responses: list[Response] = field(default_factory=list)
    repaired: dict[str, int] = field(default_factory=dict)  # region -> repair rounds used

    def flagged(self) -> dict[str, list[str]]:
        return {rid: f.problems() for rid, f in self.findings.items() if f.problems()}


def validate_and_repair(mapper: Mapper, plans: dict[str, RegionPlan], specs: dict[str, dict[str, Any]],
                        table: TableFile, rounds: int = REPAIR_ROUNDS, known: Known | None = None) -> Validated:
    """Dry run, send problem regions back to the model (at most `rounds` times), keep each region's best spec.

    Summary rows under a region (TOTAL, Summe, ...) are cut off before every dry run, without a model call.
    """
    specs = dict(specs)
    best: dict[str, tuple[int, dict[str, Any]]] = {}
    responses: list[Response] = []
    repaired: dict[str, int] = {}
    for round_number in range(rounds + 1):
        specs = {rid: exclude_summary_rows(spec, table) for rid, spec in specs.items()}
        _, findings = dry_run(specs, table, known)
        for rid, f in findings.items():
            if rid not in best or f.score() < best[rid][0]:
                best[rid] = (f.score(), specs[rid])
        problems = [rid for rid, f in findings.items() if f.problems() and rid in plans]
        if not problems or round_number == rounds:
            break
        for rid in problems:
            try:
                step = mapper.repair(plans[rid], specs[rid], findings[rid].to_dict())
            except StepError:
                continue
            responses += step.responses
            specs[rid] = step.value
            repaired[rid] = round_number + 1
    chosen = {rid: spec for rid, (_, spec) in best.items()}
    results, findings = dry_run(chosen, table, known)
    chosen = {rid: apply_confidence(spec, findings[rid]) for rid, spec in chosen.items()}
    return Validated(chosen, results, findings, responses, repaired)
