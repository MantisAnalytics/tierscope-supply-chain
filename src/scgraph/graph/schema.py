"""Canonical graph schema: domains of entity and relation types with typed fields."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

# `scalar` holds a str, int, float or bool, for EAV-style values such as risk indicators.
VALUE_TYPES = ("str", "int", "float", "bool", "date", "scalar")

KEY_SEPARATOR = "::"
# RiskIndicator category of flags parsed from text (e.g. Risk Notes): indicator = flag name, value = true.
FLAG_CATEGORY = "flag"


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: str
    # For identity fields that name another entity (e.g. SUPPLIES.site -> Site).
    ref: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class EntityType:
    name: str
    domain: str
    fields: tuple[FieldDef, ...]
    # Key parts; a single "id" for most entities.
    identity: tuple[str, ...] = ("id",)

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.name == name), None)


@dataclass(frozen=True)
class RelationType:
    name: str
    domain: str
    src: str
    dst: str
    # Context fields that are part of the edge identity, after (src, dst).
    identity: tuple[FieldDef, ...] = ()
    fields: tuple[FieldDef, ...] = ()
    # Fields recorded per source row (exploded BOM positions) rather than per edge.
    occurrence_fields: tuple[str, ...] = ()

    def field(self, name: str) -> FieldDef | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def key_names(self) -> tuple[str, ...]:
        return ("src", "dst", *(f.name for f in self.identity))


def _fields(spec: str) -> tuple[FieldDef, ...]:
    """Parse "name:type name:type ..." into field definitions."""
    return tuple(FieldDef(*item.split(":")) for item in spec.split())


@dataclass
class Schema:
    entities: dict[str, EntityType] = field(default_factory=dict)
    relations: dict[str, RelationType] = field(default_factory=dict)
    domains: list[str] = field(default_factory=list)

    def add(self, item: EntityType | RelationType) -> None:
        if item.name in self.entities or item.name in self.relations:
            raise SchemaError(f"duplicate type {item.name!r}")
        target = self.entities if isinstance(item, EntityType) else self.relations
        target[item.name] = item
        if item.domain not in self.domains:
            self.domains.append(item.domain)

    def describe(self) -> dict[str, Any]:
        """Domain -> entity and relation types with their fields and field types."""
        out: dict[str, Any] = {d: {"entities": {}, "relations": {}} for d in self.domains}
        for e in self.entities.values():
            out[e.domain]["entities"][e.name] = {
                "identity": list(e.identity),
                "fields": {f.name: f.type for f in e.fields},
            }
        for r in self.relations.values():
            out[r.domain]["relations"][r.name] = {
                "from": r.src,
                "to": r.dst,
                "identity": {f.name: f.ref or f.type for f in r.identity},
                "fields": {f.name: f.type for f in r.fields},
                "occurrence_fields": list(r.occurrence_fields),
            }
        return out

    def entity(self, name: str) -> EntityType:
        if name not in self.entities:
            raise SchemaError(f"unknown entity type {name!r}")
        return self.entities[name]

    def relation(self, name: str) -> RelationType:
        if name not in self.relations:
            raise SchemaError(f"unknown relation type {name!r}")
        return self.relations[name]

    def kind_of(self, type_name: str) -> str:
        if type_name in self.entities:
            return "entity"
        if type_name in self.relations:
            return "relation"
        raise SchemaError(f"unknown type {type_name!r}")

    def field_def(self, type_name: str, field_name: str) -> FieldDef:
        owner = self.entities.get(type_name) or self.relations.get(type_name)
        if owner is None:
            raise SchemaError(f"unknown type {type_name!r}")
        fdef = owner.field(field_name)
        if fdef is None:
            raise SchemaError(f"unknown field {field_name!r} on {type_name}")
        return fdef

    def validate_key(self, type_name: str, key: tuple) -> None:
        if type_name in self.entities:
            names = self.entities[type_name].identity
            optional: set[str] = set()
        else:
            rel = self.relation(type_name)
            names = rel.key_names
            optional = {f.name for f in rel.identity if f.optional}
        if len(key) != len(names):
            raise SchemaError(f"{type_name} key needs {len(names)} parts {names}, got {len(key)}")
        for name, part in zip(names, key, strict=True):
            if part is None and name in optional:
                continue
            if not isinstance(part, str) or not part:
                raise SchemaError(f"{type_name} key part {name!r} must be a non-empty string, got {part!r}")


def check_value(fdef: FieldDef, value: Any) -> None:
    """Raise SchemaError if `value` does not fit the field's declared type."""
    ok = {
        "str": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
        "date": _is_iso_date,
        "scalar": lambda v: isinstance(v, str | int | float | bool),
    }[fdef.type]
    if not ok(value):
        raise SchemaError(f"field {fdef.name!r} expects {fdef.type}, got {value!r}")


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def key_id(key: tuple) -> str:
    """Node id for an entity key: the single part, or the parts joined by `::`."""
    return key[0] if len(key) == 1 else KEY_SEPARATOR.join(key)


PLACE = "address:str city:str postal_code:str country:str region:str latitude:float longitude:float"


def build_schema() -> Schema:
    s = Schema()
    # core
    s.add(EntityType("Site", "core", _fields(
        f"name:str site_type:str {PLACE} recovery_time_months:float notes:str photo_url:str")))
    s.add(EntityType("Supplier", "core", _fields(
        f"name:str {PLACE} tier:int risk_score_internal:float risk_notes:str nda_signed:bool "
        "contact_email:str photo_url:str supplies_material:str")))
    s.add(EntityType("Component", "core", _fields(
        "description:str part_number:str business_unit:str commodity:str sub_commodity:str "
        "criticality:str single_source:bool annual_spend:float currency:str unit:str lead_time_days:int "
        "moq:int annual_volume:float production_address:str notes:str photo_url:str")))
    s.add(EntityType("Assembly", "core", _fields("business_unit:str revenue_eur:float revenue_multiplier:float")))
    s.add(RelationType(
        "CONTAINS", "core", "Component", "Component",
        identity=(FieldDef("assembly", "str", ref="Assembly"),),
        fields=_fields("quantity:float unit:str level:int notes:str"),
        occurrence_fields=("quantity", "unit", "level", "notes"),
    ))
    s.add(RelationType(
        "SUPPLIES", "core", "Supplier", "Component",
        identity=(FieldDef("site", "str", ref="Site", optional=True),),
    ))
    s.add(RelationType("DELIVERED_TO", "core", "Component", "Site"))
    # tiers
    # stated_confidence: the source's own claim about the link (e.g. "Medium"), not extraction confidence.
    s.add(RelationType("PARENT_SUPPLIER", "tiers", "Supplier", "Supplier", fields=_fields("stated_confidence:str")))
    # risk
    s.add(EntityType(
        "RiskIndicator", "risk",
        _fields("value:scalar source_text:str last_updated:date expiry:date notes:str"),
        identity=("supplier", "category", "indicator"),
    ))
    s.add(RelationType("HAS_RISK", "risk", "Supplier", "RiskIndicator"))
    return s


SCHEMA = build_schema()
