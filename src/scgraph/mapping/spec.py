"""Mapping spec: a declarative description of how table regions map to facts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scgraph.graph.schema import SCHEMA, Schema, SchemaError
from scgraph.mapping.transforms import TransformError, check_transform

ROLES = ("fact", "derived", "ignore")
AUTHORS = ("human", "model")
STATUSES = ("draft", "approved")


class SpecError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ValueRef:
    """A key part read from a column, or a constant."""

    column: str | None = None
    const: Any = None

    @classmethod
    def parse(cls, data: Any, where: str) -> ValueRef:
        if isinstance(data, dict) and set(data) == {"column"}:
            return cls(column=data["column"])
        if isinstance(data, dict) and set(data) == {"const"}:
            return cls(const=data["const"])
        raise SpecError([f"{where}: expected {{'column': ...}} or {{'const': ...}}, got {data!r}"])


def _refs(data: Any, where: str) -> tuple[ValueRef, ...]:
    """One ref, or a list of refs for a multi-part key."""
    items = data if isinstance(data, list) else [data]
    return tuple(ValueRef.parse(item, where) for item in items)


@dataclass(frozen=True)
class Produce:
    kind: str  # entity | relation
    type: str
    key: tuple[ValueRef, ...] = ()  # entity key parts
    src: tuple[ValueRef, ...] = ()
    dst: tuple[ValueRef, ...] = ()
    identity: dict[str, ValueRef] = field(default_factory=dict)
    when_present: tuple[str, ...] = ()

    def columns(self) -> list[str]:
        refs = [*self.key, *self.src, *self.dst, *self.identity.values()]
        return [r.column for r in refs if r.column] + list(self.when_present)


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    role: str
    target_type: str | None = None
    target_field: str | None = None
    transform: Any = None
    confidence: float | None = None
    metric: str | None = None
    entity: str | None = None
    key: ValueRef | None = None  # for derived columns of entities the region does not produce
    rationale: str | None = None


@dataclass(frozen=True)
class RegionSpec:
    id: str
    sheet: str | None
    header_row: int | None
    start: int
    end: int | None
    stop_at_blank: str | None
    confidence: float
    produces: tuple[Produce, ...]
    columns: tuple[ColumnSpec, ...]


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    sheet: str | None
    cell: str
    transform: Any = None
    label_cell: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class KnownAnomaly:
    """A derived cell known to be wrong in the source; its oracle value is kept and marked."""

    sheet: str | None
    cell: str
    reason: str


@dataclass
class MappingSpec:
    version: int
    file_kind: str
    regions: list[RegionSpec]
    parameters: list[ParameterSpec]
    ignore_sheets: dict[str, str]
    metadata: dict[str, Any]
    raw: dict[str, Any]
    known_anomalies: list[KnownAnomaly] = field(default_factory=list)
    # The spec file it was read from, when known; facts name it as their origin.
    name: str | None = None


def load_spec(path: str | Path, schema: Schema = SCHEMA) -> MappingSpec:
    spec = parse_spec(json.loads(Path(path).read_text()), schema)
    spec.name = str(path)
    return spec


def parse_spec(data: dict[str, Any], schema: Schema = SCHEMA) -> MappingSpec:
    """Parse and validate against the schema; raises SpecError listing every problem."""
    errors: list[str] = []
    regions: list[RegionSpec] = []
    for i, r in enumerate(data.get("regions", [])):
        try:
            regions.append(_region(r, i))
        except SpecError as e:
            errors.extend(e.errors)
    parameters = []
    for p in data.get("parameters", []):
        label = p.get("label") or {}
        parameters.append(ParameterSpec(p["name"], p.get("sheet"), p["cell"], p.get("transform"),
                                        label.get("cell"), label.get("equals")))
    anomalies = []
    for a in data.get("known_anomalies", []):
        if not a.get("cell") or not a.get("reason"):
            errors.append(f"known anomaly needs 'cell' and 'reason': {a!r}")
            continue
        anomalies.append(KnownAnomaly(a.get("sheet"), a["cell"].upper(), a["reason"]))
    spec = MappingSpec(
        version=data.get("version", 1),
        file_kind=data.get("file_kind", "xlsx"),
        regions=regions,
        parameters=parameters,
        ignore_sheets=dict(data.get("ignore_sheets", {})),
        metadata=dict(data.get("metadata", {})),
        raw=data,
        known_anomalies=anomalies,
    )
    errors.extend(validate(spec, schema))
    if errors:
        raise SpecError(errors)
    return spec


def _region(r: dict[str, Any], index: int) -> RegionSpec:
    rid = r.get("id") or f"region{index}"
    rows = r.get("rows", {})
    produces = []
    for p in r.get("produces", []):
        where = f"region {rid}"
        if not isinstance(p.get("when_present", []), list):
            raise SpecError([f"{where}: when_present must be a list of column headers, got {p['when_present']!r}"])
        if "entity" in p:
            produces.append(Produce("entity", p["entity"], key=_refs(p["key"], f"{where} {p['entity']} key"),
                                    when_present=tuple(p.get("when_present", []))))
        elif "relation" in p:
            name = p["relation"]
            produces.append(Produce(
                "relation", name,
                src=_refs(p["from"], f"{where} {name} from"),
                dst=_refs(p["to"], f"{where} {name} to"),
                identity={k: ValueRef.parse(v, f"{where} {name} {k}") for k, v in p.get("identity", {}).items()},
                when_present=tuple(p.get("when_present", [])),
            ))
        else:
            raise SpecError([f"region {rid}: produce entry needs 'entity' or 'relation': {p!r}"])
    columns = []
    for name, c in r.get("columns", {}).items():
        target_type = target_field = None
        if target := c.get("target"):
            target_type, _, target_field = target.partition(".")
        columns.append(ColumnSpec(
            name=name,
            role=c.get("role", "fact"),
            target_type=target_type,
            target_field=target_field,
            transform=c.get("transform"),
            confidence=c.get("confidence"),
            metric=c.get("metric"),
            entity=c.get("entity"),
            key=ValueRef.parse(c["key"], f"region {rid} column {name!r} key") if "key" in c else None,
            rationale=c.get("rationale"),
        ))
    return RegionSpec(
        id=rid,
        sheet=r.get("sheet"),
        header_row=r.get("header_row", 1),
        start=rows.get("start", (r.get("header_row") or 0) + 1),
        end=rows.get("end"),
        stop_at_blank=rows.get("stop_at_blank"),
        confidence=r.get("confidence", 1.0),
        produces=tuple(produces),
        columns=tuple(columns),
    )


def validate(spec: MappingSpec, schema: Schema = SCHEMA) -> list[str]:
    errors = []
    if spec.version != 1:
        errors.append(f"unsupported spec version {spec.version}")
    if spec.file_kind not in ("xlsx", "csv"):
        errors.append(f"unsupported file_kind {spec.file_kind!r}")
    if (author := spec.metadata.get("author")) is not None and author not in AUTHORS:
        errors.append(f"metadata.author must be one of {AUTHORS}, got {author!r}")
    if (status := spec.metadata.get("status")) is not None and status not in STATUSES:
        errors.append(f"metadata.status must be one of {STATUSES}, got {status!r}")
    ids = [r.id for r in spec.regions]
    if len(ids) != len(set(ids)):
        errors.append("region ids must be unique")
    for region in spec.regions:
        errors.extend(_validate_region(region, schema))
    for p in spec.parameters:
        try:
            check_transform(p.transform)
        except TransformError as e:
            errors.append(f"parameter {p.name}: {e}")
    return errors


def _validate_region(region: RegionSpec, schema: Schema) -> list[str]:
    errors = []
    where = f"region {region.id}"
    if not 0.0 <= region.confidence <= 1.0:
        errors.append(f"{where}: confidence must be within 0..1")
    produced = {}
    for p in region.produces:
        produced.setdefault(p.type, p)
        try:
            if p.kind == "entity":
                ent = schema.entity(p.type)
                if len(p.key) != len(ent.identity):
                    errors.append(f"{where}: {p.type} key needs {len(ent.identity)} parts {ent.identity}")
            else:
                rel = schema.relation(p.type)
                for name in p.identity:
                    if name not in {f.name for f in rel.identity}:
                        errors.append(f"{where}: {p.type} has no identity field {name!r}")
                for f in rel.identity:
                    if f.name not in p.identity and not f.optional:
                        errors.append(f"{where}: {p.type} needs identity field {f.name!r}")
        except SchemaError as e:
            errors.append(f"{where}: {e}")
    for c in region.columns:
        col = f"{where}, column {c.name!r}"
        if c.role not in ROLES:
            errors.append(f"{col}: unknown role {c.role!r}")
            continue
        if c.confidence is not None and not 0.0 <= c.confidence <= 1.0:
            errors.append(f"{col}: confidence must be within 0..1")
        try:
            check_transform(c.transform)
        except TransformError as e:
            errors.append(f"{col}: {e}")
        if c.role == "fact":
            if not c.target_type or not c.target_field:
                errors.append(f"{col}: fact column needs a target 'Type.field'")
                continue
            try:
                schema.field_def(c.target_type, c.target_field)
            except SchemaError as e:
                errors.append(f"{col}: target field {c.target_type}.{c.target_field}: {e}")
                continue
            if c.target_type not in produced:
                errors.append(f"{col}: target type {c.target_type} is not produced by the region")
        elif c.role == "derived":
            if not c.metric or not c.entity:
                errors.append(f"{col}: derived column needs 'metric' and 'entity'")
            elif c.entity not in schema.entities:
                errors.append(f"{col}: unknown entity type {c.entity!r}")
            elif c.key is None and c.entity not in produced:
                errors.append(f"{col}: entity {c.entity} is not produced by the region; give a 'key'")
    return errors
