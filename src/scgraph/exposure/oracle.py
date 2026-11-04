"""Compare computed exposure with the oracle values loaded from derived columns."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from scgraph.exposure.compute import Exposure
from scgraph.graph.facts import SourceLocation
from scgraph.graph.store import GraphStore

REL_TOL = 1e-6
ABS_TOL = 1e-9

# (entity type, metric) -> how to compute it; None means "not available" for that entity.
METRICS: dict[tuple[str, str], Callable[[Exposure, str], float | None]] = {
    ("Component", "business_exposure_pct"): lambda e, i: e.business_exposure("Component", i),
    ("Component", "business_exposure_adjusted_pct"): lambda e, i: e.adjusted_exposure(i),
    ("Site", "business_exposure_pct"): lambda e, i: e.business_exposure("Site", i),
    ("Supplier", "business_exposure_pct"): lambda e, i: e.business_exposure("Supplier", i),
    ("Supplier", "business_impact_eur"): lambda e, i: e.business_impact(i),
}


CATEGORIES = ("matched", "mismatched", "known_anomalies", "oracle_only", "computed_only")


@dataclass(frozen=True)
class Comparison:
    entity_type: str
    entity_id: str
    metric: str
    computed: float | None
    oracle: float | None
    source: SourceLocation | None = None
    anomaly: str | None = None


@dataclass
class OracleReport:
    matched: list[Comparison] = field(default_factory=list)
    mismatched: list[Comparison] = field(default_factory=list)
    # Differences on cells the spec lists as known source errors; they do not fail the check.
    known_anomalies: list[Comparison] = field(default_factory=list)
    oracle_only: list[Comparison] = field(default_factory=list)  # oracle value, nothing computed
    computed_only: list[Comparison] = field(default_factory=list)  # computed, no oracle value

    def by_metric(self) -> dict[tuple[str, str], dict[str, int]]:
        out = {k: dict.fromkeys(CATEGORIES, 0) for k in METRICS}
        for name in CATEGORIES:
            for c in getattr(self, name):
                out[(c.entity_type, c.metric)][name] += 1
        return out


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def check(store: GraphStore, exposure: Exposure | None = None) -> OracleReport:
    exposure = exposure or Exposure.from_store(store)
    oracle = {(o.entity_type, o.entity_id, o.metric): o for o in store.oracle_values()}
    report = OracleReport()
    for (entity_type, metric), compute in METRICS.items():
        known = set(exposure.ids(entity_type))
        for entity_id in sorted(known | {i for (t, i, m) in oracle if t == entity_type and m == metric}):
            ov = oracle.get((entity_type, entity_id, metric))
            computed = compute(exposure, entity_id) if entity_id in known else None
            c = Comparison(entity_type, entity_id, metric, computed, ov.value if ov else None,
                           ov.source if ov else None, ov.anomaly if ov else None)
            if ov is None:
                if computed is not None:
                    report.computed_only.append(c)
            elif computed is None:
                report.oracle_only.append(c)
            elif close(computed, ov.value):
                report.matched.append(c)
            elif ov.anomaly:
                report.known_anomalies.append(c)
            else:
                report.mismatched.append(c)
    return report
