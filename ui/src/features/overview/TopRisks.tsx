import { useState } from "react";
import type { Overview, Ranked } from "@/api/client";
import { TypeIcon } from "@/components/TypeIcon";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, formatEur, formatPct } from "@/lib/utils";
import { RevenuePending } from "./Kpis";

const LISTS = [
  { key: "top_suppliers_by_impact", label: "Suppliers by impact", metric: "impact" },
  { key: "top_suppliers_by_exposure", label: "Suppliers by exposure", metric: "exposure" },
  { key: "top_sites_by_exposure", label: "Sites by exposure", metric: "exposure" },
] as const;

export function TopRisks({ overview, onSelect }: { overview: Overview; onSelect: (type: string, id: string) => void }) {
  const [tab, setTab] = useState(0);
  const list = LISTS[tab];
  const rows: Ranked[] = overview[list.key];
  return (
    <Card role="region" aria-label="Top risks">
      <CardHeader>
        <CardTitle>Top risks</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div role="tablist" className="flex gap-1 rounded-lg bg-surface-2 p-0.5">
          {LISTS.map((l, i) => (
            <button
              key={l.key}
              role="tab"
              aria-selected={i === tab}
              onClick={() => setTab(i)}
              className={cn("flex-1 cursor-pointer rounded-md px-2 py-1 text-xs text-muted", i === tab && "bg-surface font-medium text-fg shadow-sm")}
            >
              {l.label}
            </button>
          ))}
        </div>
        {!overview.exposure_available ? (
          <p className="text-sm text-muted" data-testid="exposure-unavailable">
            <RevenuePending overview={overview} />
          </p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {rows.map((r, i) => (
              <li key={r.id} className="flex items-center gap-2 text-sm">
                <span className="w-4 text-xs text-muted tabular">{i + 1}</span>
                <TypeIcon type={r.type} />
                <button className="cursor-pointer truncate text-left hover:underline" title={r.name} onClick={() => onSelect(r.type, r.id)}>
                  {r.name}
                </button>
                <span className={cn("ml-auto shrink-0 font-medium tabular", list.metric === "impact" && "text-risk")}>
                  {list.metric === "impact" ? formatEur(r.impact_eur) : formatPct(r.exposure_pct)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
