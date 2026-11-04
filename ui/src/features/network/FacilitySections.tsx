import type { Network, NetworkEntity, NodeDetail } from "@/api/client";
import { TypeIcon } from "@/components/TypeIcon";
import { formatEur, formatPct, formatValue } from "@/lib/utils";
import { chainOf } from "./chain";
import { entityKey, tierOf } from "./filters";
import { OpenCell, Section } from "./PanelSection";

type Open = (type: string, id: string) => void;
type OpenLink = (supplier: string, site: string) => void;

/** Business Impact, exposure, tier and country of a site or supplier, from the network. */
export function FacilityFigures({ entity, exposureAvailable }: { entity: NetworkEntity; exposureAvailable: boolean }) {
  return (
    <dl className="grid grid-cols-4 gap-2 px-4 pb-3 text-sm" aria-label="Key figures">
      <div>
        <dt className="text-xs text-muted">Business Impact</dt>
        <dd className="font-semibold text-risk tabular">{formatEur(entity.impact_eur)}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted">Exposure</dt>
        <dd className="font-semibold tabular">{exposureAvailable ? formatPct(entity.exposure_pct, 2) : "—"}</dd>
      </div>
      <div>
        <dt className="text-xs text-muted">Tier</dt>
        <dd className="font-semibold tabular">{entity.type === "Supplier" ? tierOf(entity) : "site"}</dd>
      </div>
      <div className="min-w-0">
        <dt className="text-xs text-muted">Country</dt>
        <dd className="truncate font-semibold" title={entity.country ?? undefined}>{entity.country ?? "—"}</dd>
      </div>
    </dl>
  );
}

/** The facility's chain (design D4) grouped by tier, sites last; a direct supply link offers its parts. */
export function ChainSection({ network, type, id, onOpen, onOpenLink }: { network: Network; type: string; id: string; onOpen: Open; onOpenLink: OpenLink }) {
  const { members } = chainOf(network, `${type}:${id}`);
  if (members.length === 0) return null;
  const direct = new Map<string, { supplier: string; site: string; parts: number }>();
  for (const l of network.supply_links) {
    if (type === "Supplier" && l.supplier === id) direct.set(`Site:${l.site}`, { ...l, parts: l.parts.length });
    if (type === "Site" && l.site === id) direct.set(`Supplier:${l.supplier}`, { ...l, parts: l.parts.length });
  }
  const groups = new Map<string, NetworkEntity[]>();
  for (const e of [...members].sort((a, b) => a.name.localeCompare(b.name))) {
    const g = e.type === "Site" ? "Sites" : `Tier ${tierOf(e)}`;
    groups.set(g, [...(groups.get(g) ?? []), e]);
  }
  const order = [...groups.keys()].sort((a, b) => (a === "Sites" ? 1 : b === "Sites" ? -1 : a.localeCompare(b, undefined, { numeric: true })));
  return (
    <Section title="Supply chain">
      <div className="flex flex-col gap-2">
        {order.map((g) => (
          <div key={g} role="group" aria-label={g}>
            <div className="py-1 text-xs font-medium">{g} ({groups.get(g)!.length})</div>
            <ul className="flex flex-col">
              {groups.get(g)!.map((e) => {
                const link = direct.get(entityKey(e));
                return (
                  <li key={entityKey(e)} className="flex items-center gap-2 border-b py-1 text-sm last:border-b-0">
                    <TypeIcon type={e.type} />
                    <button className="min-w-0 cursor-pointer truncate text-left hover:underline" title={e.name} onClick={() => onOpen(e.type, e.id)}>
                      {e.name}
                    </button>
                    {link && (
                      <button className="ml-auto shrink-0 cursor-pointer text-xs text-interactive tabular hover:underline" onClick={() => onOpenLink(link.supplier, link.site)}>
                        {link.parts} {link.parts === 1 ? "part" : "parts"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}

/** The parts a supplier ships, or a site receives, from the network's supply links. */
export function PartsSection({ network, type, id, onOpen }: { network: Network; type: string; id: string; onOpen: Open }) {
  const name = (t: string, i: string) => network.entities.find((e) => e.type === t && e.id === i)?.name ?? i;
  const other = type === "Supplier" ? "Site" : "Supplier";
  const rows = network.supply_links
    .filter((l) => (type === "Supplier" ? l.supplier : l.site) === id)
    .flatMap((l) => l.parts.map((p) => ({ ...p, other: type === "Supplier" ? l.site : l.supplier })))
    .sort((a, b) => (b.annual_spend ?? 0) - (a.annual_spend ?? 0) || a.id.localeCompare(b.id));
  if (rows.length === 0) return null;
  return (
    <Section title={`Parts (${rows.length})`}>
      <table className="w-full table-fixed text-sm" aria-label="Parts">
        <colgroup>
          <col />
          <col className="w-24" />
          <col className="w-16" />
          <col className="w-7" />
          <col className="w-14" />
        </colgroup>
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1 font-medium">Part</th>
            <th className="py-1 font-medium">{other}</th>
            <th className="py-1 text-right font-medium">Spend</th>
            <th className="py-1 text-center font-medium" title="Single source">SS</th>
            <th className="py-1 text-right font-medium">Exposure</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.id}>${p.other}`} className="border-t">
              <td className="py-1 pr-2"><OpenCell label={p.id} sub={p.description} onClick={() => onOpen("Component", p.id)} /></td>
              <td className="py-1 pr-1">
                <button className="block w-full cursor-pointer truncate text-left text-xs hover:underline" title={name(other, p.other)} onClick={() => onOpen(other, p.other)}>
                  {name(other, p.other)}
                </button>
              </td>
              <td className="py-1 text-right tabular">{formatEur(p.annual_spend)}</td>
              <td className="py-1 text-center">{p.single_source ? <span className="text-risk" aria-label="single source">●</span> : <span className="text-muted" aria-label="multiple sources">–</span>}</td>
              <td className="py-1 text-right tabular">{formatPct(p.exposure_pct, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

/** The supplier's risk indicators with their values, from the node's HAS_RISK edges. */
export function RisksSection({ node, onOpen }: { node: NodeDetail; onOpen: Open }) {
  const risks = (node.edges ?? []).filter((e) => e.type === "HAS_RISK" && e.direction === "out").map((e) => e.other);
  if (risks.length === 0) return null;
  const v = (r: (typeof risks)[number], f: string) => r.values?.[f];
  return (
    <Section title={`Risks (${risks.length})`}>
      <table className="w-full table-fixed text-sm" aria-label="Risks">
        <colgroup>
          <col />
          <col className="w-28" />
          <col className="w-28" />
        </colgroup>
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1 font-medium">Indicator</th>
            <th className="py-1 font-medium">Value</th>
            <th className="py-1 font-medium">Source</th>
          </tr>
        </thead>
        <tbody>
          {risks.map((r) => (
            <tr key={r.id} className="border-t align-top">
              <td className="py-1 pr-2">
                <OpenCell label={formatValue(v(r, "indicator") ?? r.name)} sub={v(r, "category") != null ? String(v(r, "category")) : null} onClick={() => onOpen(r.type, r.id)} />
              </td>
              <td className="truncate py-1 pr-2" title={formatValue(v(r, "value"))}>{formatValue(v(r, "value"))}</td>
              <td className="py-1">
                <div className="truncate text-xs" title={formatValue(v(r, "source_text"))}>{formatValue(v(r, "source_text"))}</div>
                <div className="text-xs text-muted tabular">{formatValue(v(r, "last_updated"))}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}
