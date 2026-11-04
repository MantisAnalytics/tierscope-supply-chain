import { TypeIcon } from "@/components/TypeIcon";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatEur, formatPct } from "@/lib/utils";
import { byMetric, metricValue, type Slice } from "./filters";

/** The visible slice in numbers. Business Impact is not added up: two suppliers can reach the same assemblies
 *  (design D6), so the largest one is shown instead of a sum. */
export function SliceSummary({ slice, onSelect }: { slice: Slice; onSelect: (type: string, id: string) => void }) {
  const sites = slice.entities.filter((e) => e.type === "Site").length;
  const suppliers = slice.entities.length - sites;
  const largest = slice.entities.filter((e) => e.impact_eur != null).sort(byMetric("impact"))[0];
  const single = new Set(slice.supply.flatMap((l) => l.parts.filter((p) => p.single_source).map((p) => p.id))).size;
  const top = [...slice.entities].sort(byMetric(slice.metric)).slice(0, 5);
  return (
    <Card role="region" aria-label="Visible slice">
      <CardHeader>
        <CardTitle>In view</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <dl className="grid grid-cols-3 gap-2 text-sm">
          <div><dt className="text-xs text-muted">Suppliers</dt><dd className="font-semibold tabular" data-testid="slice-suppliers">{suppliers}</dd></div>
          <div><dt className="text-xs text-muted">Sites</dt><dd className="font-semibold tabular" data-testid="slice-sites">{sites}</dd></div>
          <div><dt className="text-xs text-muted">Single-source parts</dt><dd className="font-semibold tabular" data-testid="slice-single">{single}</dd></div>
        </dl>
        {largest && (
          <p className="text-sm">
            <span className="text-muted">Largest Business Impact: </span>
            <button className="cursor-pointer hover:underline" onClick={() => onSelect(largest.type, largest.id)}>{largest.name}</button>{" "}
            <span className="font-medium text-risk tabular">{formatEur(largest.impact_eur)}</span>
          </p>
        )}
        <ol className="flex flex-col gap-1.5" aria-label="Top five in view">
          {top.map((e, i) => (
            <li key={`${e.type}:${e.id}`} className="flex items-center gap-2 text-sm">
              <span className="w-4 text-xs text-muted tabular">{i + 1}</span>
              <TypeIcon type={e.type} />
              <button className="cursor-pointer truncate text-left hover:underline" title={e.name} onClick={() => onSelect(e.type, e.id)}>{e.name}</button>
              <span className={slice.metric === "impact" ? "ml-auto shrink-0 font-medium text-risk tabular" : "ml-auto shrink-0 font-medium tabular"}>
                {slice.metric === "impact" ? formatEur(metricValue(e, "impact")) : formatPct(metricValue(e, "exposure"))}
              </span>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
