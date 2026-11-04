import type { NetworkEntity, SupplyLink } from "@/api/client";
import { formatEur } from "@/lib/utils";
import { linkSummary } from "./filters";

export interface LinkHover {
  link: SupplyLink;
  x: number;
  y: number;
}

/** Hover summary of a supply link: parts, spend, single-source count and the top parts by spend. */
export function LinkTooltip({ hover, names }: { hover: LinkHover; names: Map<string, NetworkEntity> }) {
  const s = linkSummary(hover.link);
  const from = names.get(`Supplier:${hover.link.supplier}`)?.name ?? hover.link.supplier;
  const to = names.get(`Site:${hover.link.site}`)?.name ?? hover.link.site;
  return (
    <div
      role="tooltip"
      aria-label="Supply link"
      className="pointer-events-none absolute z-20 w-64 -translate-x-1/2 translate-y-3 rounded-lg border bg-surface p-3 text-xs shadow-lg"
      style={{ left: hover.x, top: hover.y }}
    >
      <div className="truncate font-medium text-fg">{from} → {to}</div>
      <dl className="mt-1.5 grid grid-cols-2 gap-y-0.5">
        <dt className="text-muted">Parts</dt>
        <dd className="text-right tabular">{s.parts}</dd>
        <dt className="text-muted">Annual spend</dt>
        <dd className="text-right tabular">{formatEur(s.spend)}</dd>
        <dt className="text-muted">Single source</dt>
        <dd className="text-right tabular">{s.single}</dd>
      </dl>
      <ul className="mt-1.5 flex flex-col gap-0.5 border-t pt-1.5" aria-label="Top parts">
        {s.top.map((p) => (
          <li key={p.id} className="flex gap-2">
            <span className="truncate">{p.description ?? p.id}</span>
            <span className="ml-auto shrink-0 text-muted tabular">{formatEur(p.annual_spend)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-muted">Click for every part.</p>
    </div>
  );
}
