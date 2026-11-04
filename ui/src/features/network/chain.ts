import type { Network, NetworkEntity } from "@/api/client";
import { entityKey } from "./filters";
import type { NodeRef } from "./view";

/** The supply chain of one facility (design D4): every key in it, and its members apart from the facility itself. */
export interface Chain {
  keys: Set<string>;
  members: NetworkEntity[];
}

/** Breadth-first from `start` over `next`, without `start` itself. */
function reach(start: string[], next: (id: string) => string[]): Set<string> {
  const seen = new Set<string>();
  const queue = [...start];
  while (queue.length) {
    for (const n of next(queue.shift()!)) {
      if (!seen.has(n) && !start.includes(n)) {
        seen.add(n);
        queue.push(n);
      }
    }
  }
  return seen;
}

/** A supplier's chain: the suppliers upstream of it through tier links, the suppliers it feeds downstream, and the
 *  sites they supply. A site's chain: its suppliers and everything upstream of them. */
export function chainOf(network: Network, key: string): Chain {
  const upstream = new Map<string, string[]>(); // parent -> suppliers that feed it
  const downstream = new Map<string, string[]>(); // supplier -> the parents it feeds
  for (const l of network.tier_links) {
    upstream.set(l.parent, [...(upstream.get(l.parent) ?? []), l.supplier]);
    downstream.set(l.supplier, [...(downstream.get(l.supplier) ?? []), l.parent]);
  }
  const up = (id: string) => upstream.get(id) ?? [];
  const [type, id] = [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)];
  const keys = new Set<string>([key]);
  if (type === "Supplier") {
    const feeds = reach([id], (s) => downstream.get(s) ?? []);
    for (const s of [...reach([id], up), ...feeds]) keys.add(`Supplier:${s}`);
    const shipping = new Set([id, ...feeds]);
    for (const l of network.supply_links) if (shipping.has(l.supplier)) keys.add(`Site:${l.site}`);
  } else if (type === "Site") {
    const direct = [...new Set(network.supply_links.filter((l) => l.site === id).map((l) => l.supplier))];
    for (const s of [...direct, ...reach(direct, up)]) keys.add(`Supplier:${s}`);
  }
  const members = network.entities.filter((e) => keys.has(entityKey(e)) && entityKey(e) !== key);
  return { keys, members };
}

/** The suppliers and sites on the supply links that carry any of these parts. */
export function facilitiesOfParts(network: Network, parts: Iterable<string>): Set<string> {
  const wanted = new Set(parts);
  const keys = new Set<string>();
  for (const l of network.supply_links) {
    if (l.parts.some((p) => wanted.has(p.id))) keys.add(`Supplier:${l.supplier}`).add(`Site:${l.site}`);
  }
  return keys;
}

/** What the network highlights for the deepest crumb of the side panel: a facility's chain, the two ends of a
 *  link, or the facilities a part or assembly comes from and goes to. `components` is an assembly's BOM. */
export function highlightOf(network: Network, crumb: NodeRef | null, components?: string[]): Set<string> | null {
  if (!crumb) return null;
  switch (crumb.type) {
    case "Site":
    case "Supplier":
      return chainOf(network, `${crumb.type}:${crumb.id}`).keys;
    case "Link": {
      const [supplier, site] = crumb.id.split(">");
      return new Set([`Supplier:${supplier}`, `Site:${site}`]);
    }
    case "Component":
      return facilitiesOfParts(network, [crumb.id]);
    case "Assembly":
      return components ? facilitiesOfParts(network, components) : null;
    case "RiskIndicator":
      return chainOf(network, `Supplier:${crumb.id.split("::")[0]}`).keys;
    default:
      return null;
  }
}
