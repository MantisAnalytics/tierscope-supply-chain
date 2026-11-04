"""Entity resolution: which node a mention in a document means, by id, normalised name, or model choice."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from scgraph.graph.store import GraphStore
from scgraph.graph.tables import UNRESOLVED_PREFIX
from scgraph.ingest.prompts import load_prompt
from scgraph.ingest.step import call_tool
from scgraph.llm import LLMClient, Response

LEGAL_FORMS = {"inc", "gmbh", "co", "ltd", "llc", "sas", "srl", "spa", "ag", "bv", "sa", "kg", "corp", "company",
               "limited"}
NAME_FIELDS = {"Supplier": "name", "Site": "name", "Component": "description"}
CONTEXT_FIELDS = ("country", "city")
NAME_CONFIDENCE = 0.95
MAX_CANDIDATES = 5
MIN_SIMILARITY = 0.6
BATCH = 10


def normalise_name(text: str) -> str:
    """Case-fold, punctuation to spaces, legal-form tokens dropped: "Acme Co., Ltd." -> "acme"."""
    words = re.sub(r"[^\w\s]", " ", text.casefold()).split()
    return " ".join(w for w in words if w not in LEGAL_FORMS)


@dataclass
class Resolution:
    id: str
    method: str  # id | name | model | unresolved
    confidence: float
    mention: str
    candidates: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        out = {"method": self.method, "mention": self.mention, "chosen": self.id, "confidence": self.confidence}
        if self.candidates:
            out["candidates"] = self.candidates
        return out

    @property
    def unresolved(self) -> bool:
        return self.method == "unresolved"


@dataclass
class Pending:
    type: str
    mention: str
    quote: str
    candidates: list[dict[str, Any]]


SUBMIT_RESOLUTIONS = {
    "name": "submit_resolutions",
    "description": "For each mention, the candidate id it refers to, or none, with a confidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "resolutions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"mention_id": {"type": "integer"},
                                   "choice": {"type": "string", "description": "a candidate id, or none"},
                                   "confidence": {"type": "number"}},
                    "required": ["mention_id", "choice", "confidence"],
                },
            }
        },
        "required": ["resolutions"],
    },
}


class Resolver:
    """Resolves mentions against the graph and the entities the current document describes."""

    def __init__(self, store: GraphStore | None = None) -> None:
        self.ids: dict[str, dict[str, str]] = {}  # type -> casefolded id -> id
        self.info: dict[str, dict[str, dict[str, Any]]] = {}  # type -> id -> fields used for names and context
        if store is not None:
            fields = store.active_fields()
            for type_name, node_id, _ in store.nodes():
                self.add(type_name, node_id, fields.get(("node", type_name, node_id), {}))

    def add(self, type_name: str, node_id: str, fields: dict[str, Any] | None = None) -> None:
        self.ids.setdefault(type_name, {})[node_id.strip().casefold()] = node_id
        info = self.info.setdefault(type_name, {}).setdefault(node_id, {})
        info.update({k: v for k, v in (fields or {}).items() if v is not None})

    def _name(self, type_name: str, node_id: str) -> str | None:
        name = self.info.get(type_name, {}).get(node_id, {}).get(NAME_FIELDS.get(type_name, "name"))
        return str(name) if name else None

    def exact(self, type_name: str, mention: str) -> Resolution | None:
        found = self.ids.get(type_name, {}).get(mention.strip().casefold())
        return Resolution(found, "id", 1.0, mention) if found else None

    def by_name(self, type_name: str, mention: str) -> Resolution | None:
        target = normalise_name(mention)
        if not target:
            return None
        hits = [i for i in self.info.get(type_name, {}) if (n := self._name(type_name, i)) and normalise_name(n) == target]
        return Resolution(hits[0], "name", NAME_CONFIDENCE, mention) if len(hits) == 1 else None

    def candidates(self, type_name: str, mention: str) -> list[dict[str, Any]]:
        target = normalise_name(mention)
        scored = []
        for node_id in self.info.get(type_name, {}):
            name = self._name(type_name, node_id)
            if not name:
                continue
            ratio = difflib.SequenceMatcher(None, target, normalise_name(name)).ratio()
            if ratio > MIN_SIMILARITY:
                info = self.info[type_name][node_id]
                scored.append((-ratio, node_id, {"id": node_id, "name": name,
                                                 **{k: info[k] for k in CONTEXT_FIELDS if k in info}}))
        return [c for _, _, c in sorted(scored)[:MAX_CANDIDATES]]

    def resolve_local(self, type_name: str, mention: str) -> Resolution | None:
        return self.exact(type_name, mention) or self.by_name(type_name, mention)


def unresolved(type_name: str, mention: str, candidates: list[str] | None = None) -> Resolution:
    return Resolution(f"{UNRESOLVED_PREFIX}{' '.join(mention.split())}", "unresolved", 0.0, mention, candidates or [])


def adjudicate(client: LLMClient | None, pending: list[Pending]) -> tuple[list[Resolution], list[Response]]:
    """Model chooses among candidates, in batches; no client or no candidates means unresolved."""
    out: list[Resolution | None] = [None] * len(pending)
    responses: list[Response] = []
    asked = [i for i, p in enumerate(pending) if p.candidates and client is not None]
    for start in range(0, len(asked), BATCH):
        batch = asked[start:start + BATCH]

        def parse(data: dict[str, Any], batch: list[int] = batch) -> dict[int, tuple[str, float]]:
            result = {}
            for r in data["resolutions"]:
                k = int(r["mention_id"])
                if not 0 <= k < len(batch):
                    raise ValueError(f"unknown mention_id {k}")
                ids = {c["id"] for c in pending[batch[k]].candidates}
                choice = str(r["choice"]).strip()
                if choice.casefold() != "none" and choice not in ids:
                    raise ValueError(f"mention {k}: {choice!r} is not one of its candidates")
                result[k] = (choice, max(0.0, min(1.0, float(r["confidence"]))))
            return result

        user = {"mentions": [{"mention_id": k, "type": pending[i].type, "mention": pending[i].mention,
                              "quote": pending[i].quote, "candidates": pending[i].candidates}
                             for k, i in enumerate(batch)]}
        step = call_tool(client, system=load_prompt("entity_resolve"), user=user, tool=SUBMIT_RESOLUTIONS, parse=parse)
        responses += step.responses
        for k, i in enumerate(batch):
            choice, confidence = step.value.get(k, ("none", 0.0))
            ids = [c["id"] for c in pending[i].candidates]
            if choice.casefold() == "none":
                out[i] = unresolved(pending[i].type, pending[i].mention, ids)
            else:
                out[i] = Resolution(choice, "model", confidence, pending[i].mention, ids)
    final = [r or unresolved(p.type, p.mention, [c["id"] for c in p.candidates]) for r, p in zip(out, pending,
                                                                                                strict=True)]
    return final, responses
