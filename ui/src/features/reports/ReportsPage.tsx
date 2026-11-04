import { CircleCheckBig, FlaskConical, Gauge, ShieldAlert, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useResolved, useThread, type ResolvedRow } from "@/api/agent";
import { Empty, ErrorNote, Loading } from "@/components/States";
import { Thread } from "@/features/agent/Thread";
import { cn, formatEur, formatTime } from "@/lib/utils";
import { ReportView } from "./ReportDocument";

const FIXED: { kind: "portfolio" | "scorecard" | "benchmark"; title: string; hint: string; Icon: LucideIcon }[] = [
  { kind: "portfolio", title: "Portfolio risk report", hint: "Suppliers, single sources, concentration", Icon: ShieldAlert },
  { kind: "scorecard", title: "Agent scorecard", hint: "Skill and cost per model", Icon: Gauge },
  { kind: "benchmark", title: "Agent benchmark", hint: "Skill on the scenario suite, held-out cases", Icon: FlaskConical },
];

/** Reports: resolved disruptions with what they cost and saved, each opening as its read-only thread next to its
 *  report; the portfolio risk report, the agent scorecard and the agent benchmark. `/reports/d/:id`,
 *  `/reports/portfolio`, `/reports/scorecard`, `/reports/benchmark`. */
export default function ReportsPage() {
  const params = useParams();
  const kind = params.kind ?? "portfolio";
  const id = kind === "d" && params.id && /^\d+$/.test(params.id) ? Number(params.id) : undefined;
  const { data: resolved = [], error, isLoading } = useResolved();

  return (
    <div className="grid h-full grid-cols-[320px_1fr]" data-print="page">
      <aside className="flex min-h-0 flex-col border-r bg-surface" data-print="hide">
        <div className="border-b px-4 py-3">
          <h1 className="text-sm font-semibold">Reports</h1>
        </div>
        <nav aria-label="Reports" className="min-h-0 flex-1 overflow-auto">
          <ul>
            {FIXED.map((r) => (
              <ReportLink key={r.kind} to={`/reports/${r.kind}`} current={kind === r.kind} title={r.title} hint={r.hint} Icon={r.Icon} />
            ))}
          </ul>
          <h2 className="px-4 pt-4 pb-1.5 text-xs font-medium text-muted">Resolved disruptions</h2>
          {isLoading ? (
            <Loading />
          ) : error ? (
            <ErrorNote error={error} className="m-3" />
          ) : resolved.length === 0 ? (
            <p className="px-4 pb-3 text-xs text-muted">
              None yet. Disruptions you mark resolved in <Link to="/agent" className="text-interactive hover:underline">Agent</Link> are reported here.
            </p>
          ) : (
            <ul aria-label="Resolved disruptions">
              {resolved.map((r) => (
                <ReportLink key={r.id} to={`/reports/d/${r.id}`} current={id === r.id} title={r.name} Icon={CircleCheckBig} hint={<Figures row={r} />} />
              ))}
            </ul>
          )}
        </nav>
      </aside>
      <section className="min-h-0 overflow-auto" data-print="flow">
        {kind === "d" ? (
          id === undefined ? (
            <Empty className="flex h-full items-center justify-center">Choose a resolved disruption on the left.</Empty>
          ) : (
            <ResolvedView id={id} row={resolved.find((r) => r.id === id)} loading={isLoading} />
          )
        ) : kind === "portfolio" || kind === "scorecard" || kind === "benchmark" ? (
          <ReportView kind={kind} />
        ) : (
          <ErrorNote error={`There is no report “${kind}”.`} className="m-6" />
        )}
      </section>
    </div>
  );
}

function Figures({ row: r }: { row: ResolvedRow }) {
  return (
    <span className="flex flex-col gap-0.5 tabular">
      <span>Resolved {formatTime(r.resolved_at)}</span>
      <span>
        <span className="text-risk">{formatEur(r.do_nothing_cost)}</span> if nothing was done · plan {formatEur(r.plan_cost)}
      </span>
      <span>
        <span className="font-medium text-ok">{formatEur(r.saved)} saved</span> · model ${(r.model_cost_usd ?? 0).toFixed(4)}
      </span>
    </span>
  );
}

/** A resolved disruption: its thread, read-only, next to its report (the one recorded at resolution). Printing shows
 *  the report alone. */
function ResolvedView({ id, row, loading }: { id: number; row: ResolvedRow | undefined; loading: boolean }) {
  const { data: thread, error } = useThread(row?.thread_id ?? undefined);
  if (!row && loading) return <Loading />;
  if (!row) return <ErrorNote error={`Disruption ${id} is not resolved.`} className="m-6" />;
  return (
    <div className="grid h-full grid-cols-[minmax(0,2fr)_minmax(0,3fr)] print:block print:h-auto">
      <section aria-label="Thread" className="min-h-0 overflow-auto border-r" data-print="hide">
        <div className="border-b px-5 py-3">
          <h2 className="text-sm font-semibold">{row.name}</h2>
          <p className="text-xs text-muted">
            Resolved by {row.resolved_by} on {formatTime(row.resolved_at)}
            {row.resolution_note && <> · “{row.resolution_note}”</>}
          </p>
        </div>
        {error ? <ErrorNote error={error} className="m-4" /> : thread ? <Thread data={thread} /> : row.thread_id !== null ? <Loading /> : null}
      </section>
      <div className="min-h-0 overflow-auto print:overflow-visible">
        <ReportView kind="disruption" id={id} />
      </div>
    </div>
  );
}

function ReportLink({ to, current, title, hint, Icon }: { to: string; current: boolean; title: string; hint: ReactNode; Icon: LucideIcon }) {
  return (
    <li>
      <Link
        to={to}
        aria-current={current ? "page" : undefined}
        className={cn("flex items-start gap-2.5 border-b px-4 py-2.5 hover:bg-surface-2", current && "bg-interactive-soft hover:bg-interactive-soft")}
      >
        <Icon className={cn("mt-0.5 size-4 shrink-0 text-muted", current && "text-interactive")} aria-hidden />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="text-sm leading-snug font-medium">{title}</span>
          <span className="flex items-center text-xs text-muted">{hint}</span>
        </span>
      </Link>
    </li>
  );
}
