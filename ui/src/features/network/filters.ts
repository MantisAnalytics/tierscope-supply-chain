import type { Network, NetworkEntity, SupplyLink, TierLink } from "@/api/client";
import type { Metric, NetworkView } from "./view";

/** What both modes and the slice summary draw: the facilities left by the filters and the links between them. */
export interface Slice {
  entities: NetworkEntity[];
  supply: SupplyLink[];
  tier: TierLink[];
  metric: Metric;
}

export const entityKey = (e: { type: string; id: string }) => `${e.type}:${e.id}`;
export const linkKey = (l: { supplier: string; site: string }) => `${l.supplier}>${l.site}`;

/** A supplier with no stated tier counts as a direct (tier-1) supplier. */
export const tierOf = (e: NetworkEntity) => (e.type === "Supplier" ? (e.tier ?? 1) : null);

/** The metric that sizes and ranks: the chosen one, else impact, or exposure when impact is unavailable. */
export function resolveMetric(view: NetworkView, network: Network): Metric {
  if (view.metric) return view.metric;
  return network.entities.some((e) => e.impact_eur != null) ? "impact" : "exposure";
}

export const metricValue = (e: NetworkEntity, metric: Metric): number | null =>
  metric === "impact" ? (e.impact_eur ?? null) : (e.exposure_pct ?? null);

/** Highest first; facilities without a value last, then by id so the order is stable. */
export function byMetric(metric: Metric) {
  return (a: NetworkEntity, b: NetworkEntity) => {
    const x = metricValue(a, metric);
    const y = metricValue(b, metric);
    if (x !== y) return x === null ? 1 : y === null ? -1 : y - x;
    return a.id.localeCompare(b.id);
  };
}

export function matches(e: NetworkEntity, view: NetworkView): boolean {
  if (!view.types.includes(e.type as "Site" | "Supplier")) return false;
  if (e.type === "Supplier" && !view.tiers.includes(tierOf(e)!)) return false;
  if (view.country.length && !view.country.includes(e.country ?? "")) return false;
  if (view.risk && e.risk_flags === 0) return false;
  if (view.ss && e.single_source_parts === 0) return false;
  return true;
}

/** Filters in this order: type, tier, country, risk, single source, then the top N by the metric. `keep` (the
 *  selection's chain, or what a part in the panel highlights) stays visible whatever the filters say. A link is
 *  kept only when both its ends are. */
export function filterNetwork(network: Network, view: NetworkView, keep?: Set<string> | null): Slice {
  const metric = resolveMetric(view, network);
  let entities = network.entities.filter((e) => matches(e, view));
  if (view.top !== null) entities = [...entities].sort(byMetric(metric)).slice(0, view.top);
  if (keep?.size) {
    const shown = new Set(entities.map(entityKey));
    entities = [...entities, ...network.entities.filter((e) => keep.has(entityKey(e)) && !shown.has(entityKey(e)))];
  }
  const shown = new Set(entities.map(entityKey));
  return {
    entities,
    metric,
    supply: network.supply_links.filter((l) => shown.has(`Supplier:${l.supplier}`) && shown.has(`Site:${l.site}`)),
    tier: network.tier_links.filter((l) => shown.has(`Supplier:${l.supplier}`) && shown.has(`Supplier:${l.parent}`)),
  };
}

/** Values the filter drawer offers, from the whole network. */
export function options(network: Network) {
  const countries = [...new Set(network.entities.map((e) => e.country).filter((c): c is string => !!c))].sort();
  const tiers = [...new Set(network.entities.map(tierOf).filter((t): t is number => t !== null))].sort();
  return { countries, tiers };
}

/** Marker size in [0, 1] by the metric on a square-root scale, so area follows the value. */
export function sizer(entities: NetworkEntity[], metric: Metric) {
  const max = Math.max(0, ...entities.map((e) => metricValue(e, metric) ?? 0));
  return (e: NetworkEntity) => {
    const v = metricValue(e, metric);
    return max > 0 && v !== null ? Math.sqrt(Math.max(0, v) / max) : 0;
  };
}

/** Parts summary of a supply link, for the hover tooltip and the link panel. */
export function linkSummary(link: SupplyLink) {
  const spend = link.parts.reduce((sum, p) => sum + (p.annual_spend ?? 0), 0);
  const single = link.parts.filter((p) => p.single_source).length;
  const top = [...link.parts].sort((a, b) => (b.annual_spend ?? 0) - (a.annual_spend ?? 0)).slice(0, 3);
  return { parts: link.parts.length, spend, single, top };
}
