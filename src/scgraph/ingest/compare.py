"""Comparison of a generated graph with the graph a reference spec builds from the same file."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scgraph.graph.facts import Fact
from scgraph.graph.schema import key_id
from scgraph.graph.store import GraphStore
from scgraph.ingest.layout import load_stored, normalise_header
from scgraph.ingest.pipeline import IngestError, resolve_specs
from scgraph.ingest.profile import Profiler
from scgraph.mapping.execute import execute
from scgraph.mapping.spec import MappingSpec, load_spec, parse_spec


@dataclass
class Agreement:
    matched: int = 0
    total: int = 0
    missing: list[Any] = field(default_factory=list)  # in reference, not generated
    extra: list[Any] = field(default_factory=list)  # in generated, not reference

    @property
    def share(self) -> float:
        return self.matched / self.total if self.total else 1.0


@dataclass
class Comparison:
    nodes: Agreement
    edges: Agreement
    fields: Agreement
    role_differences: list[tuple[str, str, str, str]]  # sheet, header, reference role, generated role
    derived_missing: list[tuple[str, str]]  # reference-derived columns not derived in the generated specs


def _graph(specs: list[MappingSpec], profiler: Profiler) -> GraphStore:
    store = GraphStore(":memory:", review=False)
    facts, oracle, params = [], [], []
    for spec in specs:
        result = execute(spec, profiler.table)
        facts += result.facts
        oracle += result.oracle
        params += result.parameters
    store.load(facts, oracle=oracle, parameters=params)
    return store


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


def _roles(specs: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    out = {}
    for spec in specs:
        for region in spec.get("regions", []):
            keys = {r["column"] for p in region["produces"] for part in ("key", "from", "to")
                    for r in (p.get(part) if isinstance(p.get(part), list) else [p.get(part)]) if r and "column" in r}
            keys |= {r["column"] for p in region["produces"] for r in p.get("identity", {}).values() if "column" in r}
            for header in keys:
                out[(region["sheet"], normalise_header(header))] = "key"
            for header, column in region["columns"].items():
                out[(region["sheet"], normalise_header(header))] = column["role"]
    return out


SLICE_RELATIONS = {"SUPPLIES": ("Supplier", "Component"), "DELIVERED_TO": ("Component", "Site"),
                   "PARENT_SUPPLIER": ("Supplier", "Supplier")}


def slice_nodes(facts: list[Fact], assembly: str) -> set[tuple[str, str]]:
    """An assembly, the components of its BOM, their suppliers (with tier-2+ suppliers below them) and sites."""
    bom = [f.key for f in facts if f.kind == "relation" and f.type == "CONTAINS" and f.key[2] == assembly]
    components = {k[0] for k in bom} | {k[1] for k in bom}
    sites = {f.key[1] for f in facts if f.kind == "relation" and f.type == "DELIVERED_TO" and f.key[0] in components}
    suppliers = {f.key[0] for f in facts if f.kind == "relation" and f.type == "SUPPLIES" and f.key[1] in components}
    changed = True
    while changed:
        changed = False
        for f in facts:
            if f.kind == "relation" and f.type == "PARENT_SUPPLIER" and f.key[1] in suppliers \
                    and f.key[0] not in suppliers:
                suppliers.add(f.key[0])
                changed = True
    return ({("Assembly", assembly)} | {("Component", c) for c in components} | {("Site", s) for s in sites}
            | {("Supplier", s) for s in suppliers})


def in_slice(fact: Fact, nodes: set[tuple[str, str]], assembly: str) -> bool:
    if fact.type in ("Assembly", "Component", "Site", "Supplier"):
        return (fact.type, key_id(fact.key)) in nodes
    if fact.type == "CONTAINS":
        return fact.key[2] == assembly
    if fact.type in SLICE_RELATIONS:
        src, dst = SLICE_RELATIONS[fact.type]
        return (src, fact.key[0]) in nodes and (dst, fact.key[1]) in nodes
    return False


def reference_graph(spec: Path, file: Path, slice_assembly: str | None = None) -> GraphStore:
    """The graph a reference spec builds from a file, optionally restricted to one assembly's slice."""
    result = execute(load_spec(spec), file)
    facts = result.facts
    if slice_assembly:
        nodes = slice_nodes(facts, slice_assembly)
        facts = [f for f in facts if in_slice(f, nodes, slice_assembly)]
    store = GraphStore(":memory:", review=False)
    store.load(facts, oracle=[] if slice_assembly else result.oracle,
               parameters=[] if slice_assembly else result.parameters)
    return store


def compare_graphs(ref: GraphStore, gen: GraphStore) -> Comparison:
    """Share of the reference's node keys, edges and field values found in the generated graph."""

    def agreement(ref_items: set, gen_items: set) -> Agreement:
        return Agreement(len(ref_items & gen_items), len(ref_items), sorted(ref_items - gen_items, key=str),
                         sorted(gen_items - ref_items, key=str))

    nodes = agreement({(t, i) for t, i, _ in ref.nodes()}, {(t, i) for t, i, _ in gen.nodes()})
    edges = agreement({(e.type, e.key) for e in ref.edges()}, {(e.type, e.key) for e in gen.edges()})
    ref_fields = {(*owner, f): v for owner, fs in ref.active_fields().items() for f, v in fs.items()}
    gen_fields = {(*owner, f): v for owner, fs in gen.active_fields().items() for f, v in fs.items()}
    fields = Agreement(total=len(ref_fields))
    for key, value in ref_fields.items():
        if key in gen_fields and _same(value, gen_fields[key]):
            fields.matched += 1
        else:
            fields.missing.append((key, value, gen_fields.get(key)))
    fields.extra = sorted(set(gen_fields) - set(ref_fields), key=str)
    return Comparison(nodes, edges, fields, [], [])


def compare(path: Path, reference: Path, specs_dir: Path, *, reference_file: Path | None = None,
            slice_assembly: str | None = None, client: Any = None) -> Comparison:
    """Generated graph of `path` against the reference spec's graph of `reference_file` (default `path`).

    Tables use their stored generated specs; documents are ingested again (model answers come from the cache).
    """
    from scgraph.ingest.docingest import ingest_document
    from scgraph.ingest.documents import TEXT_SUFFIXES

    ref = reference_graph(reference, reference_file or path, slice_assembly)
    if path.suffix.lower() in (".pdf", *TEXT_SUFFIXES):
        gen = GraphStore(":memory:", review=False)
        ingest_document(path, client, store=gen, specs_dir=specs_dir, reuse_only=client is None)
        return compare_graphs(ref, gen)
    profiler = Profiler(path)
    reused, unmatched = resolve_specs(profiler, load_stored(specs_dir))
    if unmatched:
        raise IngestError(f"no generated spec in {specs_dir} for sheet(s): {', '.join(map(str, unmatched))}")
    generated = [s.data for stored in reused.values() for s in stored]
    result = compare_graphs(ref, _graph([parse_spec(d) for d in generated], profiler))

    ref_roles = _roles([json.loads(reference.read_text())])
    gen_roles = _roles(generated)
    differences = sorted((s, h, r, gen_roles.get((s, h), "unmapped")) for (s, h), r in ref_roles.items()
                         if gen_roles.get((s, h), "unmapped") != r)
    result.role_differences = differences
    result.derived_missing = [(s, h) for s, h, r, _ in differences if r == "derived"]
    return result
