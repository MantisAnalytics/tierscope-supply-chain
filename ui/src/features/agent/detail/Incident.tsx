import { ChevronRight, FileText, ListChecks } from "lucide-react";
import type { ReactNode } from "react";
import type { DisruptionDetail, Task } from "@/api/ops";
import { Badge } from "@/components/ui/badge";
import { WeeklyChart, type Series } from "@/components/WeeklyChart";
import { cn, formatEur } from "@/lib/utils";
import type { DetailCrumb } from "../address";
import { Tile, TypeIcon } from "../shared";

const TASK_TONE: Record<Task["status"], string> = { open: "text-proposed", in_progress: "text-interactive", done: "text-ok", cancelled: "text-muted" };

/** The incident state of a disruption thread: the impact (cost if nothing is done, first week of lost output, the
 *  weekly chart), the current plan version with its decisions, the tasks and the report. */
export function Incident({ detail, onOpen }: { detail: DisruptionDetail; onOpen: (crumb: DetailCrumb) => void }) {
  const d = detail.disruption;
  const nothing = detail.references.do_nothing.outcome;
  const plan = detail.plan;
  const accepted = detail.accepted;
  const series: Series[] = [{ name: "Do nothing", values: nothing.weekly_lost_revenue }];
  if (plan?.outcome) series.push({ name: `Plan v${plan.version}`, values: plan.outcome.weekly_lost_revenue });
  if (accepted.outcome) series.push({ name: "Accepted plan", values: accepted.outcome.weekly_lost_revenue });
  const counts = plan
    ? { accepted: plan.actions.filter((a) => a.status === "accepted").length, rejected: plan.actions.filter((a) => a.status === "rejected").length }
    : null;
  return (
    <div className="flex flex-col gap-4 p-4" aria-label="Incident">
      <header className="flex items-start gap-3">
        <span className="mt-0.5 rounded-lg bg-risk-soft p-2">
          <TypeIcon type={d.type} className="size-5 text-risk" />
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-base leading-tight font-semibold">{d.name}</h2>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
            <Badge>{d.type_label}</Badge>
            <span className="tabular">week {d.start_week} to {d.start_week + d.duration_weeks - 1}</span>
            <span>· from {d.source}</span>
            {d.status !== "active" && <Badge className="text-proposed">{d.status}</Badge>}
          </div>
        </div>
      </header>
      {d.description && <p className="text-sm text-muted">{d.description}</p>}
      <section aria-label="Impact" className="grid grid-cols-2 gap-2">
        <Tile label="Cost if nothing is done" value={formatEur(nothing.total_cost)} tone="bad" />
        <Tile label="First week of lost output" value={nothing.first_stop_week === null ? "none" : `week ${nothing.first_stop_week}`}
          hint={`${detail.assessment.affected_components.length} parts affected`} />
      </section>
      <WeeklyChart series={series} label="Lost revenue per week" height={160} />
      <section aria-label="Current plan" className="flex flex-col gap-1.5">
        <h3 className="text-xs font-medium text-muted">Current plan</h3>
        {plan && counts ? (
          <Row onClick={() => onOpen({ kind: "plan", id: String(plan.version) })}>
            <ListChecks className="size-4 shrink-0 text-brand" aria-hidden />
            <span className="font-medium">Plan v{plan.version}</span>
            <span className="text-xs text-muted tabular">
              {formatEur(plan.outcome?.total_cost)} · {plan.actions.length} actions · {counts.accepted} accepted · {counts.rejected} rejected
            </span>
            {plan.stale && <Badge className="text-proposed">stale</Badge>}
          </Row>
        ) : (
          <p className="text-sm text-muted">No plan yet.</p>
        )}
      </section>
      <section aria-label="Tasks" className="flex flex-col gap-1.5">
        <h3 className="text-xs font-medium text-muted">Tasks ({detail.tasks.length})</h3>
        {detail.tasks.length === 0 ? (
          <p className="text-sm text-muted">No tasks yet: accepted actions become tasks.</p>
        ) : (
          detail.tasks.map((t) => (
            <Row key={t.id} onClick={() => onOpen({ kind: "task", id: String(t.id) })}>
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              <span className="shrink-0 text-xs text-muted">{t.owner ?? "no owner"} · week {t.due_week ?? "?"}</span>
              <Badge className={cn("shrink-0", TASK_TONE[t.status])}>{t.status.replace("_", " ")}</Badge>
            </Row>
          ))
        )}
      </section>
      <Row onClick={() => onOpen({ kind: "report", id: "" })}>
        <FileText className="size-4 shrink-0 text-muted" aria-hidden />
        <span>Report</span>
      </Row>
    </div>
  );
}

function Row({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="flex w-full cursor-pointer items-center gap-2 rounded-lg border bg-surface px-3 py-2 text-left text-sm hover:bg-surface-2">
      {children}
      <ChevronRight className="ml-auto size-4 shrink-0 text-muted" aria-hidden />
    </button>
  );
}
