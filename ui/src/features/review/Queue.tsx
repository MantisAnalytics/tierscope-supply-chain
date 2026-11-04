import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef } from "react";
import type { Proposal } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/input";
import { cn, formatEur, formatNumber } from "@/lib/utils";
import { DependencyHint } from "./DependencyHint";

export const KINDS = ["mapping", "statement", "resolution", "conflict", "restore"];
export const STATUSES = ["pending", "accepted", "rejected", "all"];

export function impactLabel(p: Proposal): string {
  const impact = p.impact;
  if (!impact) return p.status === "pending" ? "impact ?" : "";
  if (impact.no_effect) return "no exposure effect";
  if (impact.max_impact_eur > 0) return `up to ${formatEur(impact.max_impact_eur)}`;
  return `up to ${impact.max_exposure_pp.toFixed(1)} pp exposure`;
}

function confidence(p: Proposal): string {
  const [lo, hi] = p.confidence;
  if (lo == null) return "—";
  return lo === hi ? lo.toFixed(2) : `${lo.toFixed(2)}–${hi!.toFixed(2)}`;
}

export function Queue({ proposals, selected, onSelect, status, kind, onStatus, onKind }: {
  proposals: Proposal[];
  selected: number | undefined;
  onSelect: (id: number) => void;
  status: string;
  kind: string;
  onStatus: (s: string) => void;
  onKind: (k: string) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: proposals.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => 104,
    overscan: 6,
    initialRect: { width: 420, height: 800 },
  });
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b p-3">
        <Select aria-label="Status" value={status} onChange={(e) => onStatus(e.target.value)} className="h-8 text-xs">
          {STATUSES.map((s) => <option key={s} value={s}>{s === "all" ? "All statuses" : s}</option>)}
        </Select>
        <Select aria-label="Kind" value={kind} onChange={(e) => onKind(e.target.value)} className="h-8 text-xs">
          <option value="">All kinds</option>
          {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <span className="ml-auto text-xs text-muted tabular">{proposals.length} · by impact</span>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto" role="list" aria-label="Proposals">
        <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
          {virtual.getVirtualItems().map((item) => {
            const p = proposals[item.index];
            return (
              <div
                key={p.id}
                role="listitem"
                data-index={item.index}
                ref={virtual.measureElement}
                className="absolute inset-x-0"
                style={{ transform: `translateY(${item.start}px)` }}
              >
                <button
                  onClick={() => onSelect(p.id)}
                  aria-current={p.id === selected}
                  className={cn(
                    "flex w-full cursor-pointer flex-col gap-1 border-b px-3 py-2.5 text-left hover:bg-surface-2",
                    p.id === selected && "bg-interactive-soft hover:bg-interactive-soft",
                  )}
                >
                  <div className="flex items-center gap-1.5 text-xs text-muted">
                    <span className="font-medium text-fg tabular">#{p.id}</span>
                    <Badge className="border-border">{p.kind}</Badge>
                    {p.status !== "pending" && <Badge className="border-border">{p.status}</Badge>}
                    <span className="ml-auto tabular">{formatNumber(p.facts)} facts · conf {confidence(p)}</span>
                  </div>
                  <div className="line-clamp-2 text-sm">{p.summary}</div>
                  <div className={cn("text-xs tabular", p.impact && !p.impact.no_effect ? "font-medium text-risk" : "text-muted")}>
                    {impactLabel(p)}
                  </div>
                  <DependencyHint dependsOn={p.impact?.depends_on} className="text-xs text-muted" />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
