"""Facts from free text: document classification, per-chunk extraction with quotes, quote verification."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from scgraph.graph.facts import SourceLocation
from scgraph.graph.schema import SCHEMA
from scgraph.ingest.documents import Chunk, Document, find_quote, normalise_text
from scgraph.ingest.prompts import load_prompt
from scgraph.ingest.step import StepResult, call_tool
from scgraph.llm import LLMClient
from scgraph.mapping import transforms

EXCERPT_CHARS = 8000  # about 2k tokens
VALUE_MISSING_CAP = 0.5
# Above this share of facts not fitting the schema, the chunk is asked again with the reasons.
MAX_INVALID_SHARE = 0.2
EXTRACT_MAX_TOKENS = 16000


@dataclass
class DocClass:
    domains: list[str]
    types: list[str]
    summary: str = ""
    # The text reports a supply disruption (an outage, restriction, closure, tariff or shortage) rather than
    # describing the supply network; it becomes a draft disruption instead of graph facts.
    disruption_report: bool = False


SUBMIT_DOCUMENT = {
    "name": "submit_document",
    "description": "Submit the schema domains and types the document's text states facts about, and whether it "
                   "reports a supply disruption.",
    "input_schema": {
        "type": "object",
        "properties": {
            "domains": {"type": "array", "items": {"type": "string", "enum": SCHEMA.domains}},
            "types": {"type": "array", "items": {"type": "string", "enum": [*SCHEMA.entities, *SCHEMA.relations]}},
            "summary": {"type": "string"},
            "disruption_report": {"type": "boolean", "description": "true when the text reports an event that "
                                  "disrupts supply (an outage, fire, restriction, closure, tariff or shortage) "
                                  "rather than describing the supply network"},
        },
        "required": ["domains", "types", "disruption_report"],
    },
}


def classify_document(client: LLMClient, doc: Document) -> StepResult[DocClass]:
    text = "\n\n".join(b.text for b in doc.blocks)
    headings = [line.strip() for line in text.splitlines()
                if line.strip() and len(line.strip()) < 80 and not line.strip().endswith(".")][:40]

    def parse(data: dict[str, Any]) -> DocClass:
        domains = [d for d in data["domains"] if d in SCHEMA.domains]
        types = [t for t in data["types"] if t in SCHEMA.entities or t in SCHEMA.relations]
        report = bool(data.get("disruption_report"))
        if not domains and not report:
            raise ValueError("give at least one schema domain")
        return DocClass(domains, types, data.get("summary", ""), report)

    user = {"file": doc.path.name, "kind": doc.kind, "headings": headings, "excerpt": text[:EXCERPT_CHARS]}
    return call_tool(client, system=load_prompt("doc_classify"), user=user, tool=SUBMIT_DOCUMENT, parse=parse)


@dataclass
class RawFact:
    """A fact as the model stated it: mentions not yet resolved, value not yet typed."""

    kind: str
    type: str
    quote: str
    confidence: float
    mention: str | None = None
    src: str | None = None
    dst: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    field: str | None = None
    value: Any = None
    # Set by verification.
    source: SourceLocation | None = None
    flagged: bool = False
    chunk: int | None = None  # index of the chunk it was extracted from, within the document


SUBMIT_FACTS = {
    "name": "submit_facts",
    "description": "Submit every fact the chunk states, each with a verbatim quote.",
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["entity", "field", "relation"]},
                        "type": {"type": "string",
                                 "description": "entity or relation type name, e.g. Component or CONTAINS; "
                                                "never a field name"},
                        "field": {"type": "string", "description": "field name, for kind field"},
                        "mention": {"type": "string", "description": "entity id or name (entity and entity field)"},
                        "from": {"type": "string", "description": "relation source mention"},
                        "to": {"type": "string", "description": "relation target mention"},
                        "identity": {"type": "object", "description": "relation identity field -> mention"},
                        "value": {"type": "string"},
                        "quote": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["kind", "type", "quote", "confidence"],
                },
            }
        },
        "required": ["facts"],
    },
}


@dataclass
class ChunkResult:
    facts: list[RawFact]
    invalid: list[str]  # facts dropped as not fitting the schema, with the reason


def parse_facts(data: dict[str, Any], allowed: set[str], *, strict: bool = True) -> ChunkResult:
    """Raw facts; `strict` rejects the whole answer (so the step retries) when too many facts are invalid."""
    items = data.get("facts")
    if isinstance(items, str):  # the list sent as a JSON string
        try:
            items = json.loads(items)
        except ValueError:
            pass
    if not isinstance(items, list):
        raise ValueError("facts must be a list")
    facts, invalid = [], []
    for i, f in enumerate(items):
        try:
            facts.append(_raw(f, allowed))
        except (ValueError, KeyError, TypeError) as e:
            invalid.append(f"facts[{i}]: {e}")
    if items and not facts or strict and items and len(invalid) / len(items) > MAX_INVALID_SHARE:
        raise ValueError(f"{len(invalid)} of {len(items)} facts do not fit the schema: " + "; ".join(invalid[:10]))
    return ChunkResult(facts, invalid)


def _raw(f: dict[str, Any], allowed: set[str]) -> RawFact:
    kind, type_name = f["kind"], f["type"]
    if type_name not in allowed:
        raise ValueError(f"type {type_name!r} is not in the schema slice")
    raw = RawFact(kind, type_name, str(f["quote"]), max(0.0, min(1.0, float(f["confidence"]))))
    is_relation = type_name in SCHEMA.relations
    if kind == "relation" or (kind == "field" and is_relation):
        if not is_relation:
            raise ValueError(f"{type_name} is not a relation type")
        rel = SCHEMA.relation(type_name)
        raw.src, raw.dst = _mention(f.get("from")), _mention(f.get("to"))
        if not raw.src or not raw.dst:
            raise ValueError(f"{type_name} needs from and to")
        names = {x.name for x in rel.identity}
        raw.identity = {k: str(v).strip() for k, v in (f.get("identity") or {}).items() if v not in (None, "")}
        if unknown := set(raw.identity) - names:
            raise ValueError(f"{type_name} has no identity field {sorted(unknown)}")
        if missing := [x.name for x in rel.identity if not x.optional and x.name not in raw.identity]:
            raise ValueError(f"{type_name} needs identity {missing}")
    elif kind in ("entity", "field"):
        if type_name not in SCHEMA.entities:
            raise ValueError(f"{type_name} is not an entity type")
        raw.mention = _mention(f.get("mention"))
        if not raw.mention:
            raise ValueError("mention is required")
    else:
        raise ValueError(f"unknown kind {kind!r}")
    if kind == "field":
        SCHEMA.field_def(type_name, f.get("field") or "")  # raises SchemaError (a ValueError) when unknown
        if f.get("value") in (None, ""):
            raise ValueError("a field fact needs a value")
        raw.field, raw.value = f["field"], f["value"]
    return raw


def _mention(value: Any) -> str | None:
    return str(value).strip() if value not in (None, "") else None


def allowed_types(doc_class: DocClass) -> set[str]:
    """Every type of the classified domains, plus the endpoint types of their relations.

    The classified types are a hint only: classification sees the start of the document, and a type first
    stated further down (a bill of materials after the component list) must not be lost.
    """
    allowed = {t.name for t in (*SCHEMA.entities.values(), *SCHEMA.relations.values()) if t.domain in doc_class.domains}
    for name in list(allowed):
        if name in SCHEMA.relations:
            rel = SCHEMA.relation(name)
            allowed |= {rel.src, rel.dst} | {f.ref for f in rel.identity if f.ref}
    return allowed


def extract_chunk(client: LLMClient, chunk: Chunk, doc_class: DocClass) -> StepResult[ChunkResult]:
    allowed = allowed_types(doc_class)
    user = {"types": sorted(allowed), "chunk": chunk.text}
    return call_tool(client, system=load_prompt("doc_extract", doc_class.domains), user=user, tool=SUBMIT_FACTS,
                     parse=lambda data: parse_facts(data, allowed),
                     lenient=lambda data: parse_facts(data, allowed, strict=False), max_tokens=EXTRACT_MAX_TOKENS)


def _value_forms(value: str) -> list[str]:
    text = normalise_text(value).casefold()
    forms = [text]
    if re.fullmatch(r"[-+]?[\d.,\s]+%?", text):
        forms.append(re.sub(r"[,\s]", "", text))
        if "." in forms[-1]:
            forms.append(forms[-1].rstrip("0").rstrip("."))
    return forms


def verify(raw: RawFact, chunk: Chunk, doc: Document) -> bool:
    """Quote must be in the chunk (else rejected); a field value missing from its quote caps confidence and flags.

    Booleans are stated as phrases ("is not single-sourced"), so only their quote is checked. The fact's location
    is narrowed to the quote's span.
    """
    span = find_quote(chunk.text, raw.quote)
    if span is None:
        return False
    raw.source = doc.location(chunk.block, chunk.start + span[0], chunk.start + span[1], quote=raw.quote)
    if raw.kind == "field" and SCHEMA.field_def(raw.type, raw.field).type != "bool":
        quote = normalise_text(raw.quote).casefold()
        quote_digits = re.sub(r"(?<=\d)[,\s](?=\d)", "", quote)
        if not any(form and (form in quote or form in quote_digits) for form in _value_forms(str(raw.value))):
            raw.confidence = min(raw.confidence, VALUE_MISSING_CAP)
            raw.flagged = True
    return True


def typed_value(raw: RawFact) -> Any:
    """The value converted to the field's type with the table transforms' defaults; raises TransformError."""
    field_type = SCHEMA.field_def(raw.type, raw.field).type
    value = transforms.apply(None, raw.value, field_type)
    if value is transforms.MISSING:
        raise transforms.TransformError(f"empty value for {raw.type}.{raw.field}")
    return value
