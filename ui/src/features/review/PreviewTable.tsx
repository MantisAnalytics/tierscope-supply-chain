import { Link } from "react-router-dom";
import type { Impact } from "@/api/client";
import { TypeIcon } from "@/components/TypeIcon";
import { explorerPath, formatEur, formatPct } from "@/lib/utils";

type Row = Impact["effects"][number] & Record<string, unknown>;

const num = (v: unknown) => (typeof v === "number" ? v : null);

/** Exposure and Business Impact before and after, per entity, largest change first. */
export function PreviewTable({ impact }: { impact: Impact }) {
  if (impact.no_effect) return <p className="text-sm text-muted">No exposure effect on the current graph.</p>;
  const rows = impact.effects as Row[];
  return (
    <div>
      <p className="mb-2 text-xs text-muted">
        {impact.changed} entities change; up to {formatEur(impact.max_impact_eur)} Business Impact and {impact.max_exposure_pp.toFixed(2)} pp exposure.
        {impact.changed > rows.length && ` Largest ${rows.length} shown.`}
      </p>
      <table className="w-full text-sm" aria-label="Exposure preview">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b">
            <th className="py-1.5 font-medium">Entity</th>
            <th className="py-1.5 text-right font-medium">Exposure before</th>
            <th className="py-1.5 text-right font-medium">after</th>
            <th className="py-1.5 text-right font-medium">Impact before</th>
            <th className="py-1.5 text-right font-medium">after</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {rows.map((e) => (
            <tr key={`${e.type}:${e.id}`} className="border-b last:border-b-0">
              <td className="py-1.5">
                <Link to={explorerPath(e.type, e.id)} className="flex items-center gap-1.5 hover:underline">
                  <TypeIcon type={e.type} /> {e.id}
                </Link>
              </td>
              <td className="py-1.5 text-right text-muted">{formatPct(num(e.exposure_pct_before), 2)}</td>
              <td className="py-1.5 text-right">{formatPct(num(e.exposure_pct_after), 2)}</td>
              <td className="py-1.5 text-right text-muted">{e.type === "Supplier" ? formatEur(num(e.impact_eur_before)) : ""}</td>
              <td className="py-1.5 text-right">{e.type === "Supplier" ? formatEur(num(e.impact_eur_after)) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
