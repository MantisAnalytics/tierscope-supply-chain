import { Link } from "react-router-dom";
import type { OpenTask } from "@/api/agent";
import { useOpsSummary } from "@/api/ops";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { agentPath } from "@/features/agent/address";
import { TypeIcon } from "@/features/agent/shared";
import { cn, formatEur } from "@/lib/utils";

/** Where a task opens: in its disruption's thread in Agent, or in Reports once the disruption is resolved. */
const taskPath = (t: OpenTask) =>
  t.disruption_status === "resolved" || t.thread_id === null ? `/reports/d/${t.disruption_id}` : agentPath(t.thread_id, [{ kind: "task", id: String(t.id) }]);

/** Active disruptions with their cost at risk and the accepted plan's cost, and the open tasks of every disruption
 *  with owner, due week and the overdue mark; each links to its thread in Agent. */
export function Operations() {
  const { data } = useOpsSummary();
  if (!data) return null;
  const tasks = data.tasks ?? [];
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Active disruptions</CardTitle>
          <Link to="/agent" className="text-xs text-interactive hover:underline">
            {data.active.length ? "Open Agent" : "Add one"}
          </Link>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {data.active.length === 0 ? (
            <p className="text-sm text-muted">No active disruptions.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.active.slice(0, 5).map((d) => (
                <li key={d.id}>
                  <Link to={d.thread_id ? agentPath(d.thread_id) : "/agent"} className="flex items-start gap-2 text-sm hover:underline">
                    <TypeIcon type={d.type} className="mt-0.5 size-4 shrink-0 text-muted" />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate">{d.name}</span>
                      <span className="text-xs text-muted tabular">
                        <span className="text-risk">{formatEur(d.do_nothing_cost)} at risk</span>
                        {d.plan_cost !== null && <> · plan {formatEur(d.plan_cost)}</>}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card aria-label="Open tasks">
        <CardHeader>
          <CardTitle>Open tasks</CardTitle>
          <span className="text-xs text-muted tabular">
            {tasks.length}
            {data.overdue_tasks ? <span className="text-risk"> · {data.overdue_tasks} overdue</span> : null}
          </span>
        </CardHeader>
        <CardContent>
          {tasks.length === 0 ? (
            <p className="text-sm text-muted">No open tasks.</p>
          ) : (
            <ul className="flex max-h-72 flex-col divide-y overflow-auto">
              {tasks.map((t) => (
                <li key={t.id}>
                  <Link to={taskPath(t)} className="flex flex-col gap-0.5 py-1.5 text-sm hover:underline" aria-label={`${t.title}, ${t.disruption_name}`}>
                    <span className="truncate">{t.title}</span>
                    <span className="flex items-center gap-1.5 text-xs text-muted">
                      <span className="truncate">{t.disruption_name}</span>
                      <span className="shrink-0">· {t.owner ?? "no owner"} · week {t.due_week ?? "?"}</span>
                      {t.overdue && <span className={cn("shrink-0 rounded bg-risk-soft px-1 font-medium text-risk")}>overdue</span>}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
