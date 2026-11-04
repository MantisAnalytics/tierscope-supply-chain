"""Deterministic spec executor: table rows -> facts, oracle values and parameters. No LLM calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scgraph.graph.facts import Fact, OracleValue, Parameter, SourceLocation
from scgraph.graph.schema import SCHEMA, Schema, key_id
from scgraph.mapping import transforms
from scgraph.mapping.spec import MappingSpec, Produce, RegionSpec, SpecError, ValueRef
from scgraph.mapping.tables import Region, Row, TableError, TableFile, is_blank


@dataclass(frozen=True)
class TransformFailure:
    region: str
    source: SourceLocation
    column: str  # header text
    raw: Any
    error: str


@dataclass(frozen=True)
class UnmappedColumn:
    region: str
    sheet: str | None
    column: str  # letter
    header: str | None


@dataclass
class ExecutionResult:
    facts: list[Fact] = field(default_factory=list)
    oracle: list[OracleValue] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    failures: list[TransformFailure] = field(default_factory=list)
    unmapped: list[UnmappedColumn] = field(default_factory=list)
    unmapped_sheets: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)  # region id -> rows read
    # (region id, row number) -> {type: key} of what that row produced, for attaching parsed text fields.
    row_keys: dict[tuple[str, int], dict[str, tuple]] = field(default_factory=dict)


def execute(spec: MappingSpec, path: str | Path | TableFile, *, run_id: str = "",
            schema: Schema = SCHEMA, spec_name: str | None = None) -> ExecutionResult:
    """Facts carry their origin {spec, region, column}: `spec_name`, else the file the spec was loaded from."""
    table = path if isinstance(path, TableFile) else TableFile(path)
    name = spec_name or spec.name or "spec"
    result = ExecutionResult()
    anomalies = {(a.sheet, a.cell): a.reason for a in spec.known_anomalies}
    used_sheets: set[str | None] = set(spec.ignore_sheets)
    for region_spec in spec.regions:
        used_sheets.add(region_spec.sheet)
        try:
            region = table.region(
                region_spec.sheet,
                header_row=region_spec.header_row,
                start=region_spec.start,
                end=region_spec.end,
                stop_at_blank=region_spec.stop_at_blank,
            )
        except TableError as e:
            raise SpecError([f"region {region_spec.id}: {e}"]) from None
        _RegionRun(region_spec, region, result, run_id, schema, anomalies, name).run()
    for p in spec.parameters:
        used_sheets.add(p.sheet)
        result.parameters.append(_parameter(p, table))
    if table.kind == "xlsx":
        result.unmapped_sheets = [s for s in table.sheet_names if s not in used_sheets]
    return result


def _parameter(p, table: TableFile) -> Parameter:
    if p.label_cell:
        found = table.cell(p.sheet, p.label_cell)
        if str(found or "").strip() != (p.label or ""):
            raise SpecError([f"parameter {p.name}: expected label {p.label!r} in {p.label_cell}, found {found!r}"])
    raw = table.cell(p.sheet, p.cell)
    try:
        value = transforms.apply(p.transform or "number", raw)
    except transforms.TransformError as e:
        raise SpecError([f"parameter {p.name}: {p.cell}: {e}"]) from None
    if value is transforms.MISSING:
        raise SpecError([f"parameter {p.name}: cell {p.cell} is empty"])
    column = "".join(ch for ch in p.cell if ch.isalpha())
    row = int(p.cell[len(column):])
    return Parameter(p.name, value, SourceLocation(file=table.name, sheet=p.sheet, row=row, column=column))


class _RegionRun:
    def __init__(self, spec: RegionSpec, region: Region, result: ExecutionResult, run_id: str, schema: Schema,
                 anomalies: dict[tuple[str | None, str], str], spec_name: str):
        self.spec, self.region, self.result, self.run_id, self.schema = spec, region, result, run_id, schema
        self.anomalies = anomalies
        self.spec_name = spec_name
        self.letters: dict[str, str] = {}
        missing = []
        names = [c.name for c in spec.columns] + [n for p in spec.produces for n in p.columns()]
        names += [c.key.column for c in spec.columns if c.key and c.key.column]
        for name in names:
            letter = region.column(name)
            if letter is None:
                missing.append(f"region {spec.id}: column {name!r} not found in sheet {spec.sheet!r}")
            else:
                self.letters[name] = letter
        if missing:
            raise SpecError(missing)

    def run(self) -> None:
        mapped = set(self.letters.values())
        for letter, header in self.region.header.items():
            has_data = any(not is_blank(row.cells.get(letter)) for row in self.region.rows)
            if letter not in mapped and has_data:
                self.result.unmapped.append(UnmappedColumn(self.spec.id, self.spec.sheet, letter, header))
        for row in self.region.rows:
            self._row(row)
        self.result.rows[self.spec.id] = len(self.region.rows)

    def origin(self, column: str | None) -> dict[str, Any]:
        """The spec column a fact came from; None for the region's keys and relations."""
        return {"spec": self.spec_name, "region": self.spec.id, "column": column}

    def _source(self, row: Row, letter: str | None) -> SourceLocation:
        return SourceLocation(file=self.region.file, sheet=self.region.sheet, row=row.number, column=letter)

    def _ref(self, row: Row, ref: ValueRef) -> str | None:
        raw = ref.const if ref.column is None else row.cells.get(self.letters[ref.column])
        if is_blank(raw):
            return None
        return transforms.apply("str", raw)

    def _row(self, row: Row) -> None:
        keys: dict[str, tuple] = {}  # type -> key of the first produce of that type in this row
        for produce in self.spec.produces:
            if any(is_blank(row.cells.get(self.letters[c])) for c in produce.when_present):
                continue
            key = self._produce(row, produce)
            if key is not None:
                keys.setdefault(produce.type, key)
        self.result.row_keys[(self.spec.id, row.number)] = keys
        for col in self.spec.columns:
            if col.role == "ignore":
                continue
            letter = self.letters[col.name]
            raw = row.cells.get(letter)
            source = self._source(row, letter)
            if col.role == "derived":
                self._derived(row, col, raw, source, keys)
                continue
            key = keys.get(col.target_type)
            if key is None:
                continue
            fdef = self.schema.field_def(col.target_type, col.target_field)
            value = self._transform(col.transform, raw, fdef.type, source, col.name)
            if value is transforms.MISSING:
                continue
            self.result.facts.append(Fact(
                "field", col.target_type, key, source, field=col.target_field, value=value,
                method="mapping", confidence=col.confidence if col.confidence is not None else self.spec.confidence,
                run_id=self.run_id, origin=self.origin(col.name),
            ))

    def _produce(self, row: Row, produce: Produce) -> tuple | None:
        if produce.kind == "entity":
            parts = [self._ref(row, r) for r in produce.key]
            if any(p is None for p in parts):
                return None
            key = tuple(parts)
            first = produce.key[0].column
        else:
            src, dst = self._joined(row, produce.src), self._joined(row, produce.dst)
            if src is None or dst is None:
                return None
            identity = [self._ref(row, produce.identity[f.name]) if f.name in produce.identity else None
                        for f in self.schema.relation(produce.type).identity]
            key = (src, dst, *identity)
            first = produce.src[0].column
        source = self._source(row, self.letters.get(first) if first else None)
        self.result.facts.append(Fact(produce.kind, produce.type, key, source, method="mapping",
                                      confidence=self.spec.confidence, run_id=self.run_id, origin=self.origin(None)))
        return key

    def _joined(self, row: Row, refs: tuple[ValueRef, ...]) -> str | None:
        parts = [self._ref(row, r) for r in refs]
        return None if any(p is None for p in parts) else key_id(tuple(parts))

    def _derived(self, row: Row, col, raw: Any, source: SourceLocation, keys: dict[str, tuple]) -> None:
        if col.key is not None:
            entity_id = self._ref(row, col.key)
        else:
            key = keys.get(col.entity)
            entity_id = key_id(key) if key else None
        if entity_id is None:
            return
        value = self._transform(col.transform or "number", raw, "float", source, col.name)
        if value is transforms.MISSING:
            return
        anomaly = self.anomalies.get((source.sheet, f"{source.column}{source.row}"))
        self.result.oracle.append(OracleValue(col.entity, entity_id, col.metric, value, source, anomaly))

    def _transform(self, spec: Any, raw: Any, field_type: str, source: SourceLocation, name: str) -> Any:
        try:
            return transforms.apply(spec, raw, field_type)
        except transforms.TransformError as e:
            self.result.failures.append(TransformFailure(self.spec.id, source, name, raw, str(e)))
            return transforms.MISSING
