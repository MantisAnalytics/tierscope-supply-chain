import { Download, Printer } from "lucide-react";
import { useId } from "react";
import { reportPath, useReport, type Report, type ReportBlock } from "@/api/ops";
import { ErrorNote, Loading } from "@/components/States";
import { Button } from "@/components/ui/button";
import { WeeklyChart } from "@/components/WeeklyChart";
import { cn, formatEur, formatNumber, formatTime } from "@/lib/utils";

/** A report as the backend writes it (sections of text, metrics, tables, charts and lists), with Markdown download
 *  and print. */
export function ReportView({ kind, id }: { kind: "portfolio" | "scorecard" | "benchmark" | "disruption"; id?: number }) {
  const { data, error, isLoading } = useReport(kind, id);
  if (isLoading) return <Loading label="Building report…" />;
  if (error || !data) return <ErrorNote error={error ?? "no report"} className="m-6" />;
  return <ReportDocument report={data} />;
}

export function ReportDocument({ report: r }: { report: Report }) {
  const download = `/api${reportPath(r.kind, r.id)}/markdown`;
  return (
    <article aria-label={r.title} className="report mx-auto flex max-w-[1120px] flex-col gap-4 p-6">
      <div className="report-head flex items-start gap-4 pb-1">
        <div className="flex min-w-0 flex-col gap-1">
          <h1 className="text-xl font-semibold tracking-tight">{r.title}</h1>
          {r.subtitle && <p className="text-sm text-muted">{r.subtitle}</p>}
          <p className="text-xs text-muted">Generated {formatTime(r.generated_at)}</p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-2" data-print="hide">
          <Button asChild variant="outline" size="sm">
            <a href={download} download>
              <Download /> Download Markdown
            </a>
          </Button>
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Printer /> Print
          </Button>
        </div>
      </div>
      {r.sections.map((s, i) => (
        <ReportSection key={i} title={s.title} note={s.note} blocks={s.blocks} />
      ))}
    </article>
  );
}

function ReportSection({ title, note, blocks }: Report["sections"][number]) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="report-section card flex flex-col gap-3 rounded-xl border bg-surface p-4">
      <div className="report-section-head flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h2 id={id} className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {note && <span className="text-xs text-muted italic">{note}</span>}
      </div>
      {blocks.map((b, i) => (
        <Block key={i} block={b} />
      ))}
    </section>
  );
}

function Block({ block }: { block: ReportBlock }) {
  switch (block.kind) {
    case "text":
      return block.text ? <p className="max-w-[80ch] text-sm leading-relaxed whitespace-pre-line">{block.text}</p> : null;
    case "metrics":
      return (
        <dl className="report-metrics grid grid-cols-[repeat(auto-fill,minmax(170px,1fr))] gap-2.5">
          {block.items.map((m) => (
            <div key={m.label} className="flex flex-col gap-0.5 rounded-lg border bg-surface-2/50 px-3 py-2">
              <dt className="text-xs text-muted">{m.label}</dt>
              <dd className="text-base font-semibold tracking-tight tabular">{m.value}</dd>
            </div>
          ))}
        </dl>
      );
    case "table":
      return <ReportTable columns={block.columns} rows={block.rows} />;
    case "chart":
      return (
        <div className="report-chart flex flex-col gap-1">
          <h3 className="text-xs font-medium text-muted">{block.title}{block.unit ? ` (${block.unit})` : ""}</h3>
          <WeeklyChart series={block.series} label={block.title} format={unitFormat(block.unit)} height={200} />
        </div>
      );
    case "list":
      return block.items.length === 0 ? null : (
        <div className={cn(block.items.length > 12 && "report-scroll max-h-[360px] overflow-auto rounded-lg border px-3 py-2")}>
          <ul className="flex list-disc flex-col gap-1 pl-5 text-sm marker:text-muted">
            {block.items.map((item, i) => (
              <li key={i} className="break-words">{item}</li>
            ))}
          </ul>
        </div>
      );
    default:
      return null;
  }
}

function unitFormat(unit: string): (v: number) => string {
  if (unit === "EUR") return formatEur;
  return (v) => `${formatNumber(Math.round(v * 100) / 100)}${unit ? ` ${unit}` : ""}`;
}

// Figures as the backend writes them: 12, 0.85, 41.2%, EUR 1.20M, $0.0040, n/a.
const NUMERIC = /^(-?(EUR|€|\$)\s?)?-?[\d,]+(\.\d+)?\s?(%|M|k|bn)?$/;
const PLACEHOLDER = new Set(["-", "—", "n/a", ""]);

function numericColumns(columns: string[], rows: (string | number | null)[][]): boolean[] {
  return columns.map((_, c) => {
    const values = rows.map((r) => r[c]).filter((v) => v !== null && !PLACEHOLDER.has(String(v)));
    return values.length > 0 && values.every((v) => typeof v === "number" || NUMERIC.test(String(v).trim()));
  });
}

function ReportTable({ columns, rows }: { columns: string[]; rows: (string | number | null)[][] }) {
  if (rows.length === 0) return <p className="text-sm text-muted">None.</p>;
  const numeric = numericColumns(columns, rows);
  return (
    <div className="report-scroll max-h-[440px] overflow-auto rounded-lg border">
      <table className="w-full border-collapse text-sm tabular">
        <thead className="sticky top-0 z-10 bg-surface-2">
          <tr>
            {columns.map((c, i) => (
              <th key={c} scope="col" className={cn("px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted", numeric[i] && "text-right")}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-t">
              {row.map((cell, i) => (
                <td key={i} className={cn("px-3 py-1.5 align-top", numeric[i] ? "text-right whitespace-nowrap" : "min-w-24")}>
                  {cell === null || cell === "" ? <span className="text-muted">—</span> : typeof cell === "number" ? formatNumber(cell) : cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
