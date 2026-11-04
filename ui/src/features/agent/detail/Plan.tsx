import { Check, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import type { PlanAction, PlanView } from "@/api/agent";
import { useDecide, useStartRun, type DisruptionDetail } from "@/api/ops";
import { useAuthor } from "@/app/author";
import { ErrorNote } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn, formatEur } from "@/lib/utils";
import { formatSkill } from "../shared";

const STATUS_TONE: Record<PlanAction["status"], string> = {
  proposed: "text-proposed",
  accepted: "text-ok",
  rejected: "text-conflict",
  superseded: "text-muted",
};

const costKey = (a: PlanAction) => (a.action.type === "priority" ? "priority" : `${a.action.type}:${a.action.component}`);

/** Actions grouped by the component they act on; priority rules (no component) under "All assemblies". */
export function groupByComponent(actions: PlanAction[]): [string, PlanAction[]][] {
  const groups = new Map<string, PlanAction[]>();
  for (const a of actions) {
    const key = a.action.component ?? "All assemblies";
    groups.set(key, [...(groups.get(key) ?? []), a]);
  }
  return [...groups.entries()];
}

/** A plan version: its actions grouped by component with cost and reason, a decision on each (a comment with a
 *  rejection), Accept all and Reject all, the stale mark with a request for a new version, the spending limit, and
 *  the plan against the playbook and the optimiser. Only the current version takes decisions. */
export function Plan({ plan, detail, canPlan, readOnly = false }: { plan: PlanView; detail: DisruptionDetail; canPlan: boolean; readOnly?: boolean }) {
  const decide = useDecide();
  const again = useStartRun();
  const { requireAuthor } = useAuthor();
  const costs = plan.outcome?.action_costs ?? {};
  const open = plan.current && !readOnly;
  const proposed = plan.actions.filter((a) => a.status === "proposed" && a.action_id !== null);
  const all = (accept: boolean) =>
    requireAuthor(async () => {
      for (const a of proposed) await decide.mutateAsync({ id: a.action_id!, accept });
    });
  const stale = plan.stale
    ? plan.stale.refused.length
      ? `${plan.stale.refused.length} action${plan.stale.refused.length === 1 ? " is" : "s are"} no longer allowed on the current graph`
      : `on the current graph it costs ${formatEur(plan.stale.total_cost_now)} instead of ${formatEur(plan.stale.total_cost_before)}`
    : null;
  return (
    <div className="flex flex-col gap-4 p-4" aria-label={`Plan v${plan.version}`}>
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold">Plan v{plan.version}</h2>
          {!plan.current && <Badge className="text-muted">superseded</Badge>}
          {plan.skill !== null && <span className="text-xs text-muted tabular">skill {formatSkill(plan.skill)}</span>}
        </div>
        <p className="text-xs text-muted tabular">
          <span className="text-risk">{formatEur(plan.do_nothing_cost)}</span> if nothing is done → <span className="text-ok">{formatEur(plan.outcome?.total_cost)}</span> with this plan
        </p>
        {plan.summary && <p className="text-sm">{plan.summary}</p>}
      </header>
      {plan.current && stale && (
        <div className="flex items-center gap-2 rounded-md border border-proposed/40 bg-proposed/10 px-3 py-2 text-xs text-proposed" role="status">
          <span className="flex-1">Stale: {stale}.</span>
          {!readOnly && (
            <Button size="xs" variant="outline" disabled={!canPlan || again.isPending} onClick={() => requireAuthor(() => again.mutate(plan.disruption_id))}>
              <RefreshCw /> Ask for a new version
            </Button>
          )}
        </div>
      )}
      {plan.over_limit && (
        <p className="rounded-md border border-conflict/40 bg-conflict/10 px-3 py-2 text-xs text-conflict">
          Action costs of {formatEur(plan.outcome?.action_cost)} exceed the spending limit of {formatEur(plan.spend_limit_eur)}.
        </p>
      )}
      {open && proposed.length > 1 && (
        <div className="flex items-center gap-2">
          <Button size="sm" variant="accent" disabled={decide.isPending} onClick={() => all(true)}>
            <Check /> Accept all
          </Button>
          <Button size="sm" variant="danger" disabled={decide.isPending} onClick={() => all(false)}>
            <X /> Reject all
          </Button>
        </div>
      )}
      {decide.error ? <ErrorNote error={decide.error} /> : null}
      {again.error ? <ErrorNote error={again.error} /> : null}
      {groupByComponent(plan.actions).map(([component, actions]) => (
        <section key={component} aria-label={component} className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-muted">{component}</h3>
          {actions.map((a, i) => (
            <ActionRow key={a.action_id ?? `${component}-${i}`} action={a} cost={costs[costKey(a)]} open={open} />
          ))}
        </section>
      ))}
      <Compared plan={plan} detail={detail} />
    </div>
  );
}

function ActionRow({ action: a, cost, open }: { action: PlanAction; cost?: number; open: boolean }) {
  const [comment, setComment] = useState("");
  const decide = useDecide();
  const { requireAuthor } = useAuthor();
  const send = (accept: boolean) =>
    requireAuthor(() => decide.mutate({ id: a.action_id!, accept, comment: comment.trim() || undefined }, { onSuccess: () => setComment("") }));
  const deciding = open && a.status === "proposed" && a.action_id !== null;
  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3" aria-label={a.text}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className={cn("text-sm font-medium", a.status === "rejected" && "text-muted line-through")}>{a.text}</span>
          {a.reason && <span className="text-xs text-muted">{a.reason}</span>}
          {a.comment && <span className="text-xs">“{a.comment}”</span>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge className={STATUS_TONE[a.status]}>{a.kept ? "kept" : a.status}</Badge>
          {cost !== undefined && <span className="text-xs text-muted tabular">costs {formatEur(cost)}</span>}
        </div>
      </div>
      {deciding && (
        <div className="flex items-center gap-2">
          <Input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Comment (with a rejection: why)" className="h-8 text-[13px]" aria-label={`Comment on ${a.text}`} />
          <Button size="sm" variant="accent" onClick={() => send(true)} disabled={decide.isPending}>
            <Check /> Accept
          </Button>
          <Button size="sm" variant="danger" onClick={() => send(false)} disabled={decide.isPending}>
            <X /> Reject
          </Button>
        </div>
      )}
      {decide.error ? <ErrorNote error={decide.error} /> : null}
    </div>
  );
}

/** The plan against doing nothing, the standard playbook, the optimiser reference and what is accepted so far. */
function Compared({ plan, detail }: { plan: PlanView; detail: DisruptionDetail }) {
  const { references: refs, accepted } = detail;
  const rows = [
    { name: "Do nothing", cost: refs.do_nothing.outcome.total_cost, n: 0, skill: 0 as number | null | undefined },
    { name: "Standard playbook", cost: refs.playbook.outcome.total_cost, n: refs.playbook.plan.length, skill: refs.playbook.skill },
    { name: "Optimiser reference", cost: refs.optimiser.outcome.total_cost, n: refs.optimiser.plan.length, skill: refs.optimiser.skill },
    ...(plan.outcome ? [{ name: `Plan v${plan.version}`, cost: plan.outcome.total_cost, n: plan.actions.length, skill: plan.skill }] : []),
    ...(accepted.outcome ? [{ name: "Accepted so far", cost: accepted.outcome.total_cost, n: accepted.plan.length, skill: accepted.skill }] : []),
  ];
  return (
    <section aria-label="Plans compared" className="flex flex-col gap-1.5">
      <h3 className="text-xs font-medium text-muted" title="Skill: cost reduction against doing nothing, as a share of the optimiser's">Plans compared</h3>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1 font-medium">Plan</th>
            <th className="py-1 text-right font-medium">Actions</th>
            <th className="py-1 text-right font-medium">Total cost</th>
            <th className="py-1 text-right font-medium">Skill</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} className="border-t">
              <td className="py-1.5">{r.name}</td>
              <td className="py-1.5 text-right tabular">{r.n}</td>
              <td className="py-1.5 text-right tabular">{formatEur(r.cost)}</td>
              <td className="py-1.5 text-right font-medium tabular">{formatSkill(r.skill)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
