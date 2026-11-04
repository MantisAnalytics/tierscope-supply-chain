import { CheckCheck, FileJson } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useApproveSpec, useSpec, useSpecs } from "@/api/queries";
import { useAuthor } from "@/app/author";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, formatTime } from "@/lib/utils";

interface Column {
  role: string;
  target?: string;
  metric?: string;
  entity?: string;
  confidence?: number;
  rationale?: string;
  approved_by?: string;
  rejected_by?: string;
}
interface Region {
  id: string;
  sheet: string;
  header_row?: number;
  rows?: Record<string, unknown>;
  confidence?: number;
  key_columns?: Record<string, Column>;
  columns?: Record<string, Column>;
  produces?: unknown[];
}

const ROLE_STYLE: Record<string, string> = {
  key: "border-interactive/40 text-interactive",
  fact: "border-ok/40 text-ok",
  derived: "border-brand/40 text-brand",
  ignore: "border-border text-muted",
};

function SpecDetail({ name }: { name: string }) {
  const { data, error, isLoading } = useSpec(name);
  const approve = useApproveSpec();
  const { requireAuthor } = useAuthor();
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorNote error={error ?? "not found"} className="m-4" />;
  const { summary, spec } = data;
  const regions = (spec.regions ?? []) as Region[];
  const threshold = 0.9;
  return (
    <article className="flex flex-col gap-4 p-5" aria-label={`Spec ${summary.name}`}>
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold break-all">{summary.name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusBadge status={summary.status} />
            <span>from {summary.source_file ?? "?"}</span>
            <span>by {summary.author ?? "?"}{summary.model ? ` (${summary.model})` : ""}</span>
            {summary.approved_by && <span>approved by {summary.approved_by}{summary.approved_at ? `, ${formatTime(summary.approved_at)}` : ""}</span>}
          </div>
        </div>
        {summary.status !== "approved" && (
          <Button
            variant="accent"
            disabled={approve.isPending}
            onClick={() => requireAuthor(() => approve.mutate(summary.name, { onSuccess: () => toast.success("Spec approved") }))}
          >
            <CheckCheck /> Approve spec
          </Button>
        )}
      </header>
      {approve.error && <ErrorNote error={approve.error} />}
      {summary.below_threshold > 0 && summary.status !== "approved" && (
        <p className="rounded-md border border-proposed/40 bg-proposed/10 px-3 py-2 text-sm">
          {summary.below_threshold} column(s) below the review threshold have no decision yet. Derived columns never get a proposal; approving the spec confirms every column.
        </p>
      )}
      {regions.length === 0 && <p className="text-sm text-muted">No regions: this spec ignores its sheet.</p>}
      {regions.map((r) => (
        <section key={r.id} className="card rounded-xl border bg-surface">
          <div className="flex items-center gap-2 border-b px-4 py-2.5 text-sm">
            <span className="font-medium">{r.id}</span>
            <span className="text-muted">sheet {r.sheet}, header row {r.header_row ?? "?"}</span>
            <ConfidenceBar value={r.confidence} className="ml-auto" />
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b">
                <th className="px-4 py-1.5 font-medium">Column</th>
                <th className="font-medium">Role</th>
                <th className="font-medium">Target</th>
                <th className="font-medium">Confidence</th>
                <th className="pr-4 font-medium">Rationale</th>
              </tr>
            </thead>
            <tbody>
              {[...Object.entries(r.key_columns ?? {}), ...Object.entries(r.columns ?? {})].map(([header, c]) => (
                <tr key={header} className={cn("border-b align-top last:border-b-0", c.role !== "ignore" && (c.confidence ?? 1) < threshold && !c.approved_by && "bg-proposed/5")}>
                  <td className="px-4 py-1.5 font-medium">{header}</td>
                  <td className="py-1.5"><Badge className={ROLE_STYLE[c.role] ?? ""}>{c.role}</Badge></td>
                  <td className="py-1.5 text-xs">{c.target ?? (c.metric ? `${c.entity ?? ""} ${c.metric}` : "—")}</td>
                  <td className="py-1.5"><ConfidenceBar value={c.confidence ?? 1} /></td>
                  <td className="py-1.5 pr-4 text-xs text-muted">{c.rationale ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </article>
  );
}

export default function SpecsPage() {
  const { name } = useParams();
  const navigate = useNavigate();
  const { data = [], error, isLoading } = useSpecs();
  const selected = name ?? data[0]?.name;
  return (
    <div className="grid h-full grid-cols-[380px_1fr]">
      <aside className="min-h-0 overflow-auto border-r bg-surface" aria-label="Mapping specs">
        {isLoading ? <Loading /> : error ? <ErrorNote error={error} className="m-3" /> : data.length === 0 ? <Empty>No stored specs.</Empty> : (
          <ul>
            {data.map((s) => (
              <li key={s.name}>
                <button
                  onClick={() => navigate(`/data/specs/${encodeURIComponent(s.name)}`)}
                  aria-current={s.name === selected}
                  className={cn("flex w-full cursor-pointer flex-col gap-1 border-b px-3 py-2.5 text-left hover:bg-surface-2", s.name === selected && "bg-interactive-soft")}
                >
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    <FileJson className="size-4 shrink-0 text-muted" />
                    <span className="truncate">{s.name.split("__").slice(1).join(" / ") || s.name}</span>
                  </span>
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <StatusBadge status={s.status} />
                    <span className="truncate">{s.sheets.join(", ") || s.region_kind}</span>
                    {s.below_threshold > 0 && s.status !== "approved" && <span className="ml-auto shrink-0">{s.below_threshold} below threshold</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="min-h-0 overflow-auto">{selected && <SpecDetail name={selected} />}</section>
    </div>
  );
}
