import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useExposure } from "@/api/queries";
import { Loading } from "@/components/States";
import { formatEur, formatPct } from "@/lib/utils";

/** Exposure figures and the assemblies the node feeds with their BOM paths; assemblies and parts open in the panel. */
export function ExposureSection({ type, id, onOpen }: { type: string; id: string; onOpen: (type: string, id: string) => void }) {
  const { data, error, isLoading } = useExposure(type, id);
  const [open, setOpen] = useState<string | null>(null);
  if (isLoading) return <Loading label="Computing exposure…" />;
  if (error) {
    const unavailable = error instanceof ApiError && error.status === 422;
    return (
      <p className="text-sm text-muted">
        {unavailable ? (
          <>Exposure unavailable: no assembly revenue is accepted yet. <Link className="text-interactive hover:underline" to="/data/review">Review queue</Link></>
        ) : (
          error.message
        )}
      </p>
    );
  }
  if (!data) return null;
  return (
    <div className="flex flex-col gap-3" data-testid="exposure">
      <dl className="grid grid-cols-2 gap-2">
        <div className="rounded-md bg-surface-2 p-2">
          <dt className="text-xs text-muted">Business exposure</dt>
          <dd className="text-lg font-semibold tabular">{formatPct(data.business_exposure_pct, 2)}</dd>
        </div>
        {data.adjusted_pct != null && (
          <div className="rounded-md bg-surface-2 p-2">
            <dt className="text-xs text-muted">Adjusted (single source ×{data.single_source_factor ?? 1})</dt>
            <dd className="text-lg font-semibold tabular">{formatPct(data.adjusted_pct, 2)}</dd>
          </div>
        )}
        {type === "Supplier" && (
          <>
            <div className="rounded-md bg-surface-2 p-2">
              <dt className="text-xs text-muted">Business Impact</dt>
              <dd className="text-lg font-semibold text-risk tabular">{formatEur(data.business_impact_eur)}</dd>
            </div>
            <div className="rounded-md bg-surface-2 p-2">
              <dt className="text-xs text-muted">Recovery time</dt>
              <dd className="text-lg font-semibold tabular">
                {data.recovery_time_months == null ? "—" : `${data.recovery_time_months} months`}
              </dd>
            </div>
          </>
        )}
      </dl>
      <div>
        <div className="mb-1 text-xs text-muted">{type === "Component" ? "Used in" : "Impacted assemblies"} ({data.impacted_assemblies.length})</div>
        <ul className="flex flex-col">
          {data.impacted_assemblies.map((a) => (
            <li key={a.id} className="border-b last:border-b-0">
              <div className="flex items-center gap-1.5 py-1.5 text-sm">
                <button
                  className="cursor-pointer text-muted"
                  onClick={() => setOpen(open === a.id ? null : a.id)}
                  aria-expanded={open === a.id}
                  aria-label={`BOM paths in ${a.id}`}
                >
                  {open === a.id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                </button>
                <button className="cursor-pointer hover:underline" onClick={() => onOpen("Assembly", a.id)}>{a.id}</button>
                <span className="ml-auto text-xs text-muted tabular">{formatEur(a.revenue_eur)}</span>
              </div>
              {open === a.id && (
                <ul className="mb-2 ml-6 flex flex-col gap-1 text-xs" aria-label={`BOM paths in ${a.id}`}>
                  {a.paths.map((p, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-1">
                      {p.map((c, j) => (
                        <span key={j} className="flex items-center gap-1">
                          {j > 0 && <span className="text-muted">›</span>}
                          <button className="cursor-pointer rounded bg-surface-2 px-1 hover:underline" onClick={() => onOpen("Component", c)}>{c}</button>
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
