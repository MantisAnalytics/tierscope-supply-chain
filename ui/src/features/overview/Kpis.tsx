import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Overview } from "@/api/client";
import { formatEur, formatNumber } from "@/lib/utils";

function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 px-4 py-2">
      <span className="truncate text-xs text-muted">{label}</span>
      <span className="text-lg leading-tight font-semibold tracking-tight tabular">{value}</span>
      {hint && <span className="truncate text-xs text-muted">{hint}</span>}
    </div>
  );
}

/** Link to the pending revenue proposal that exposure figures wait for. */
export function RevenuePending({ overview }: { overview: Overview }) {
  const [first] = overview.revenue_proposals;
  return (
    <span>
      Unavailable: revenue awaits review.{" "}
      {first !== undefined && (
        <Link to={`/data/review/${first}`} className="text-interactive hover:underline">
          Open proposal #{first}
        </Link>
      )}
    </span>
  );
}

/** Company-wide key figures as one slim strip above the network. */
export function Kpis({ overview }: { overview: Overview }) {
  const c = overview.counts;
  return (
    <section aria-label="Key figures" className="chrome grid shrink-0 grid-cols-6 divide-x border-b">
      <Tile
        label="Total assembly revenue"
        value={overview.exposure_available ? formatEur(overview.total_revenue_eur) : <span className="text-muted">—</span>}
        hint={overview.exposure_available ? `${formatNumber(c.assemblies)} assemblies` : <RevenuePending overview={overview} />}
      />
      <Tile label="Suppliers" value={formatNumber(c.suppliers)} />
      <Tile label="Sites" value={formatNumber(c.sites)} />
      <Tile label="Components" value={formatNumber(c.components)} />
      <Tile label="Single-source components" value={formatNumber(c.single_source_components)} />
      <Tile
        label="Pending proposals"
        value={formatNumber(overview.pending_total)}
        hint={<Link to="/data/review" className="text-interactive hover:underline">Review queue</Link>}
      />
    </section>
  );
}
