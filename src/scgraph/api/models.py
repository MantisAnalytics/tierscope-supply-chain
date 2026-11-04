"""Request bodies and JSON shapes of the API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from scgraph.graph.rows import FieldValue


class AcceptBody(BaseModel):
    comment: str | None = None
    target_id: str | None = None  # resolution: the node the mention means
    new_node: str | bool | None = None  # resolution: create a node (an id, or true for the mention text)


class RejectBody(BaseModel):
    comment: str | None = None


class EditsBody(BaseModel):
    edits: list[dict[str, Any]]
    evidence_id: str | None = None
    comment: str | None = None


def field_value(v: FieldValue) -> dict[str, Any]:
    source = v.source
    return {"value": v.value, "confidence": v.confidence, "method": v.method, "status": v.status,
            "review": v.review, "active": v.active, "locked": v.locked, "proposal_id": v.proposal_id,
            "source": source.to_dict(), "label": source.label(), "quote": source.quote,
            "evidence_id": source.evidence_id, "author": source.author, "resolution": v.resolution,
            "origin": v.origin}


def journal_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in ("id", "ts", "actor", "action", "summary", "undone_by", "origin")} | {
        "items": entry["items"] if isinstance(entry["items"], int) else len(entry["items"])}


# -- response shapes (the UI's types are generated from these) ---------------

class NodeRef(BaseModel):
    type: str
    id: str


class GraphNode(NodeRef):
    name: str
    referenced_only: bool = False
    pending: bool = False


class GraphEdge(BaseModel):
    id: int
    type: str
    key: list[Any]
    src: NodeRef
    dst: NodeRef
    identity: dict[str, Any]
    fields: dict[str, Any]
    occurrences: int
    pending: bool = False
    virtual: bool = False  # ROOT: an assembly to its root component; not stored


class Subgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False
    omitted: int = 0  # nodes within the depth left out by the limit


class NetworkEntity(NodeRef):
    name: str
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    tier: int | None = None
    exposure_pct: float | None = None  # None while no assembly revenue is accepted
    impact_eur: float | None = None  # suppliers; None when unavailable
    risk_flags: int = 0  # HAS_RISK neighbours
    single_source_parts: int = 0  # single-source components on its supply links


class NetworkPart(BaseModel):
    id: str
    description: str | None = None
    annual_spend: float | None = None
    single_source: bool | None = None
    exposure_pct: float | None = None


class SupplyLink(BaseModel):
    supplier: str
    site: str
    parts: list[NetworkPart]


class TierLink(BaseModel):
    supplier: str
    parent: str


class Network(BaseModel):
    version: int
    entities: list[NetworkEntity]
    supply_links: list[SupplyLink]
    tier_links: list[TierLink]
    exposure_available: bool


class Ranked(NodeRef):
    name: str
    exposure_pct: float
    impact_eur: float | None = None


class JournalEntry(BaseModel):
    id: int
    ts: str
    actor: str
    action: str
    summary: str
    items: int
    undone_by: int | None = None
    origin: str | None = None  # thread:<id> for an edit applied from an agent thread


class Overview(BaseModel):
    version: int
    total_revenue_eur: float | None  # None while no assembly revenue is accepted
    exposure_available: bool
    revenue_proposals: list[int]  # pending proposals that target assembly revenue
    counts: dict[str, int]  # assemblies, suppliers, sites, components, single_source_components
    pending_proposals: dict[str, int]  # by kind
    pending_total: int
    top_suppliers_by_impact: list[Ranked]
    top_suppliers_by_exposure: list[Ranked]
    top_sites_by_exposure: list[Ranked]
    latest_journal: list[JournalEntry]


class SpecSummary(BaseModel):
    name: str
    file: str
    status: str
    source_file: str | None = None
    author: str | None = None
    model: str | None = None
    describes: str | None = None
    region_kind: str | None = None
    sheets: list[str]
    approved_by: str | None = None
    approved_at: str | None = None
    columns_by_role: dict[str, int]
    below_threshold: int  # columns below the review threshold no person decided on



class Open(BaseModel):
    """A response shape whose listed fields are typed and whose other fields pass through unchanged."""
    model_config = ConfigDict(extra="allow")


class Stats(Open):
    nodes: dict[str, int]
    edges: dict[str, int]
    proposals: dict[str, int]  # pending, by kind
    version: int
    review_threshold: float


class SearchHit(NodeRef):
    name: str
    score: float


class FieldValueOut(Open):
    value: Any = None
    confidence: float | None = None
    method: str
    status: str
    review: str
    active: bool
    locked: bool
    proposal_id: int | None = None
    source: dict[str, Any]
    label: str
    quote: str | None = None
    evidence_id: str | None = None
    author: str | None = None
    resolution: dict[str, Any] | None = None
    origin: Any = None


class NodeSource(Open):
    source: dict[str, Any]
    label: str
    method: str
    confidence: float | None = None


class NodeSummary(NodeRef):
    """The other end of a node's edge: enough to show it in a row without fetching it."""
    name: str
    values: dict[str, Any]  # active scalar field values, and the parts of a composite id


class NodeEdge(Open):
    id: int
    type: str
    direction: str  # out or in
    key: list[Any]
    other: NodeSummary
    identity: dict[str, Any]
    fields: dict[str, list[FieldValueOut]]
    sources: list[dict[str, Any]]


class NodeDetail(Open):
    type: str
    id: str
    hidden: str | None = None  # merged or deleted: then only merged_into or deletion follows
    merged_into: str | None = None
    deletion: dict[str, Any] | None = None
    referenced_only: bool = False
    sources: list[NodeSource] = []
    fields: dict[str, list[FieldValueOut]] = {}
    edges: list[NodeEdge] = []
    root: str | None = None  # an assembly's root component
    components: list[str] | None = None  # every component in an assembly's BOM


class ExposureSummary(Open):
    type: str
    id: str
    business_exposure_pct: float
    assemblies: list[str]
    components: list[str]
    adjusted_pct: float | None = None
    recovery_time_months: float | None = None
    business_impact_eur: float | None = None


class ImpactedAssembly(BaseModel):
    id: str
    revenue_eur: float
    paths: list[list[str]]


class ExposureDetail(ExposureSummary):
    total_revenue_eur: float
    impacted_assemblies: list[ImpactedAssembly]
    single_source_factor: float | None = None


class Effect(Open):
    type: str
    id: str
    exposure_pp: float
    impact_eur: float


class Impact(Open):
    version: int
    no_effect: bool
    depends_on: list[int] = []  # pending proposals whose acceptance this one's effect waits for
    changed: int
    max_impact_eur: float
    max_exposure_pp: float
    effects: list[Effect]


class Proposal(Open):
    id: int
    kind: str  # mapping, statement, resolution, conflict, restore
    group_key: list[Any]
    status: str
    summary: str
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    comment: str | None = None
    notes: list[Any] = []
    impact: Impact | None = None
    facts: int
    confidence: list[float | None]


class FactView(Open):
    kind: str
    type: str
    key: list[Any]
    item: str
    field: str | None = None
    value: Any = None
    confidence: float | None = None
    method: str
    source: str
    location: dict[str, Any]
    quote: str | None = None
    review: str
    resolution: dict[str, Any] | None = None
    target: str
    current: Any = None


class ProposalDetail(Proposal):
    facts: list[FactView]  # type: ignore[assignment]  # samples, or all with ?samples=
    count: int
    targets: list[str]
    changes: list[dict[str, Any]] | None = None  # conflict
    spec: dict[str, Any] | None = None  # mapping: the spec column's context
    mention: str | None = None  # resolution
    candidates: list[str] | None = None  # resolution
    deletion: dict[str, Any] | None = None  # restore


class Decision(Open):
    journal: JournalEntry
    proposal: Proposal
    warnings: list[str]
    proposals_created: list[int]


class EditResult(Open):
    journal: JournalEntry
    edits: list[Any]
    proposals_created: list[int]


class EvidenceOut(BaseModel):
    id: str
    name: str
    bytes: int


class UndoResult(BaseModel):
    journal: JournalEntry


class IngestReport(Open):
    kind: str  # table or document
    file: str
    run_id: str
    errors: list[Any]
    load: dict[str, Any]
    llm: dict[str, Any]


class SpecDetail(BaseModel):
    summary: SpecSummary
    spec: dict[str, Any]
