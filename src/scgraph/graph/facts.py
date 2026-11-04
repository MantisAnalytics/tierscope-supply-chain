"""Facts: the only input to the graph store, whatever produced them."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from scgraph.graph.schema import SCHEMA, Schema, SchemaError, check_value

KINDS = ("entity", "relation", "field")
METHODS = ("mapping", "parser", "llm", "human")


class FactError(ValueError):
    pass


@dataclass(frozen=True)
class SourceLocation:
    """Where a fact came from: a table cell (sheet, row, column) or a text place (page, span, lines)."""

    file: str
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    page: int | None = None
    span: tuple[int, int] | None = None
    lines: tuple[int, int] | None = None
    quote: str | None = None  # verbatim text a model-extracted fact rests on
    # Evidence of a human edit: a link, a free-text comment, who made it, and an uploaded evidence file's id.
    url: str | None = None
    note: str | None = None
    author: str | None = None
    evidence_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceLocation:
        data = dict(data)
        for name in ("span", "lines"):
            if data.get(name) is not None:
                data[name] = tuple(data[name])
        return cls(**data)

    def label(self) -> str:
        parts = [self.file]
        if self.sheet is not None:
            parts.append(f"{self.sheet}!{self.column or ''}{self.row or ''}")
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.span is not None:
            parts.append(f"chars {self.span[0]}-{self.span[1]}")
        if self.lines is not None:
            parts.append(f"lines {self.lines[0]}-{self.lines[1]}")
        if self.quote:
            parts.append(f'"{self.quote}"')
        if self.url:
            parts.append(self.url)
        if self.note:
            parts.append(f"({self.note})")
        if self.evidence_id:
            parts.append(f"evidence {self.evidence_id[:12]}")
        if self.author:
            parts.append(f"by {self.author}")
        return " ".join(parts)


@dataclass(frozen=True)
class Fact:
    """An entity's existence, a relation, or one field value of an entity or relation.

    `key` is the entity identity, or for relations (src_id, dst_id, *identity values).
    """

    kind: str
    type: str
    key: tuple
    source: SourceLocation | None
    field: str | None = None
    value: Any = None
    method: str = "mapping"
    confidence: float = 1.0
    run_id: str = ""
    # How a mention in a document was matched to this fact's node: method, mention, candidates, chosen, confidence.
    resolution: dict[str, Any] | None = None
    # What produced the fact, for grouping review proposals: a spec column {spec, region, column}, a document
    # passage {document, chunk, quote}, or a human edit {edit}.
    origin: dict[str, Any] | None = None

    def validate(self, schema: Schema = SCHEMA) -> None:
        if self.kind not in KINDS:
            raise FactError(f"unknown fact kind {self.kind!r}")
        if self.source is None or not self.source.file:
            raise FactError(f"{self.type} {self.key}: fact has no source location")
        if self.method not in METHODS:
            raise FactError(f"unknown extraction method {self.method!r}")
        if not isinstance(self.confidence, int | float) or not 0.0 <= self.confidence <= 1.0:
            raise FactError(f"confidence must be within 0..1, got {self.confidence!r}")
        if self.origin is not None and not isinstance(self.origin, dict):
            raise FactError(f"origin must be a dict, got {self.origin!r}")
        try:
            kind = schema.kind_of(self.type)
            if self.kind in ("entity", "relation") and kind != self.kind:
                raise SchemaError(f"{self.type!r} is not a {self.kind} type")
            schema.validate_key(self.type, self.key)
            if self.kind == "field":
                if not self.field:
                    raise SchemaError("field fact without a field name")
                check_value(schema.field_def(self.type, self.field), self.value)
        except SchemaError as e:
            raise FactError(str(e)) from None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "kind": self.kind,
            "type": self.type,
            "key": list(self.key),
            "source": self.source.to_dict() if self.source else None,
            "method": self.method,
            "confidence": self.confidence,
            "run_id": self.run_id,
        }
        if self.kind == "field":
            d["field"] = self.field
            d["value"] = self.value
        if self.resolution is not None:
            d["resolution"] = self.resolution
        if self.origin is not None:
            d["origin"] = self.origin
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        source = data.get("source")
        return cls(
            kind=data["kind"],
            type=data["type"],
            key=tuple(data["key"]),
            source=SourceLocation.from_dict(source) if source else None,
            field=data.get("field"),
            value=data.get("value"),
            method=data.get("method", "mapping"),
            confidence=data.get("confidence", 1.0),
            run_id=data.get("run_id", ""),
            resolution=data.get("resolution"),
            origin=data.get("origin"),
        )


@dataclass(frozen=True)
class OracleValue:
    """A value from a derived column: kept to check our computations, never a graph fact."""

    entity_type: str
    entity_id: str
    metric: str
    value: float
    source: SourceLocation
    # Reason the spec gives when this cell is a known error in the source (e.g. typed over a formula).
    anomaly: str | None = None


@dataclass(frozen=True)
class Parameter:
    """A named model parameter read from a single cell, such as the margin."""

    name: str
    value: Any
    source: SourceLocation
