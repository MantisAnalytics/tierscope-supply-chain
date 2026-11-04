"""Prompt files and the schema slice the model sees."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from scgraph.graph.schema import SCHEMA, Schema

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

# Names the exposure code reads; the model should use them for matching derived columns and parameters.
METRICS = {
    "business_exposure_pct": "Business Exposure in % of sales (Component, Site, Supplier)",
    "business_exposure_adjusted_pct": "Business Exposure adjusted to single source, in % (Component)",
    "business_impact_eur": "Business Impact in EUR (Supplier)",
    "recovery_time_months": "recovery time in months derived from other sheets (Supplier)",
}
# Meanings that field names alone do not carry.
FIELD_HINTS = {
    "Assembly.revenue_eur": "sales revenue attributed to the assembly (may be labelled a weight); an input, "
                            "even when a formula splits a business unit's turnover",
    "Site.recovery_time_months": "months a site needs to recover from a disruption",
    "Supplier.risk_notes": "the raw text of free or semi-structured risk notes",
    "PARENT_SUPPLIER.stated_confidence": "how sure the source says it is about the link (e.g. Low/Medium/High)",
    "Supplier.supplies_material": "what the supplier supplies, as stated in text",
    "Component.business_unit": "the business unit(s) the component belongs to, as listed (e.g. 'RI; RW'); a fact",
    **{f"{t}.photo_url": "link to a photo; a fact even when a formula builds it from the id"
       for t in ("Site", "Supplier", "Component")},
}
# Meanings of relations, shown under each relation of the schema slice.
RELATION_HINTS = {
    "SUPPLIES": "the supplier supplies the component; identity site is where that supply is delivered",
    "DELIVERED_TO": "the component is delivered to the site; holds also when the site is only named as the "
                    "delivery site of a supply, so state both relations",
    "CONTAINS": "the parent component uses the child in the BOM of the identity assembly",
    "PARENT_SUPPLIER": "from a (tier-2+) supplier to its parent supplier, the one it supplies; a row's own "
                       "supplier is `from`, the parent named in that row is `to`",
}
PARAMETERS = {
    "single_source_factor.yes": "exposure factor for single-sourced components",
    "single_source_factor.no": "exposure factor for components that are not single-sourced",
    "single_source_factor.missing": "exposure factor when the single-source flag is empty",
    "margin": "gross margin as a fraction of sales",
}


def render_schema(domains: Iterable[str] | None = None, schema: Schema = SCHEMA) -> str:
    """Compact text of the schema's entity and relation types for the given domains (all when None)."""
    wanted = list(domains) if domains is not None else schema.domains
    lines = ["Schema:"]
    for domain in schema.domains:
        if domain not in wanted:
            continue
        lines.append(f"domain {domain}")
        for e in schema.entities.values():
            if e.domain == domain:
                lines.append(f"  entity {e.name}, key ({', '.join(e.identity)})")
                lines.append("    fields: " + ", ".join(f"{f.name}:{f.type}" for f in e.fields))
                lines += [f"    {h.split('.', 1)[1]}: {text}" for h, text in FIELD_HINTS.items()
                          if h.split(".", 1)[0] == e.name]
        for r in schema.relations.values():
            if r.domain != domain:
                continue
            identity = ", ".join(
                f"{f.name}: {f.ref + ' id' if f.ref else f.type}{' (optional)' if f.optional else ''}"
                for f in r.identity
            )
            lines.append(f"  relation {r.name}: {r.src} -> {r.dst}" + (f", identity ({identity})" if identity else ""))
            if r.name in RELATION_HINTS:
                lines.append(f"    meaning: {RELATION_HINTS[r.name]}")
            for side, type_name in (("from", r.src), ("to", r.dst)):
                parts = schema.entities[type_name].identity
                if len(parts) > 1:
                    lines.append(f"    `{side}` is the {type_name} key: a list of {len(parts)} refs ({', '.join(parts)})")
            if r.fields:
                per = " (one value per source row)" if r.occurrence_fields else ""
                lines.append("    fields: " + ", ".join(f"{f.name}:{f.type}" for f in r.fields) + per)
                lines += [f"    {h.split('.', 1)[1]}: {text}" for h, text in FIELD_HINTS.items()
                          if h.split(".", 1)[0] == r.name]
    lines.append("Metric names for derived columns:")
    lines += [f"  {name}: {text}" for name, text in METRICS.items()]
    lines.append("Parameter names:")
    lines += [f"  {name}: {text}" for name, text in PARAMETERS.items()]
    return "\n".join(lines)


def load_prompt(name: str, domains: Iterable[str] | None = None) -> str:
    text = (PROMPTS / f"{name}.md").read_text()
    return text.replace("{schema}", render_schema(domains)) if "{schema}" in text else text
