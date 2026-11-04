import { ArrowRight } from "lucide-react";
import type { Network } from "@/api/client";
import { TypeIcon } from "@/components/TypeIcon";
import { formatEur, formatPct } from "@/lib/utils";
import { linkSummary } from "./filters";
import type { LinkRef } from "./view";

/** A supply link: who ships to which site, and every part on it, each opening in the side panel. */
export function LinkPanel({ link, network, onSelect, onOpenPart }: {
  link: LinkRef;
  network: Network;
  onSelect: (type: string, id: string) => void;
  onOpenPart: (id: string) => void;
}) {
  const found = network.supply_links.find((l) => l.supplier === link.supplier && l.site === link.site);
  const name = (type: string, id: string) => network.entities.find((e) => e.type === type && e.id === id)?.name ?? id;
  if (!found) return <p className="p-4 text-sm text-muted">No supply link from {link.supplier} to {link.site} in the current graph.</p>;
  const s = linkSummary(found);
  return (
    <div className="flex flex-col" aria-label={`Supply link ${link.supplier} to ${link.site}`}>
      <div className="flex flex-col gap-1 px-4 pt-4 pb-3">
        <span className="text-xs text-muted">Supply link</span>
        <div className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
          <button className="flex cursor-pointer items-center gap-1 hover:underline" onClick={() => onSelect("Supplier", link.supplier)}>
            <TypeIcon type="Supplier" /> {name("Supplier", link.supplier)}
          </button>
          <ArrowRight className="size-4 text-muted" aria-hidden />
          <button className="flex cursor-pointer items-center gap-1 hover:underline" onClick={() => onSelect("Site", link.site)}>
            <TypeIcon type="Site" /> {name("Site", link.site)}
          </button>
        </div>
      </div>
      <dl className="grid grid-cols-3 gap-2 border-t px-4 py-3 text-sm">
        <div><dt className="text-xs text-muted">Parts</dt><dd className="font-semibold tabular">{s.parts}</dd></div>
        <div><dt className="text-xs text-muted">Annual spend</dt><dd className="font-semibold tabular">{formatEur(s.spend)}</dd></div>
        <div><dt className="text-xs text-muted">Single source</dt><dd className="font-semibold tabular">{s.single}</dd></div>
      </dl>
      <table className="w-full table-fixed border-t text-sm" aria-label="Parts on this link">
        <colgroup>
          <col />
          <col className="w-24" />
          <col className="w-9" />
          <col className="w-20" />
        </colgroup>
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="px-4 py-1.5 font-medium">Part</th>
            <th className="py-1.5 text-right font-medium">Spend</th>
            <th className="py-1.5 text-center font-medium" title="Single source">SS</th>
            <th className="py-1.5 pr-4 text-right font-medium">Exposure</th>
          </tr>
        </thead>
        <tbody>
          {found.parts.map((p) => (
            <tr key={p.id} className="border-t hover:bg-surface-2">
              <td className="px-4 py-1.5">
                <button className="flex w-full min-w-0 cursor-pointer flex-col text-left hover:underline" onClick={() => onOpenPart(p.id)} title={p.description ?? p.id}>
                  <span className="truncate font-medium">{p.id}</span>
                  {p.description && <span className="truncate text-xs text-muted">{p.description}</span>}
                </button>
              </td>
              <td className="py-1.5 text-right tabular">{formatEur(p.annual_spend)}</td>
              <td className="py-1.5 text-center">{p.single_source ? <span className="text-risk" aria-label="single source">●</span> : <span className="text-muted" aria-label="multiple sources">–</span>}</td>
              <td className="py-1.5 pr-4 text-right tabular">{formatPct(p.exposure_pct, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
