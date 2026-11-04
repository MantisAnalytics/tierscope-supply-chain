import { CircleAlert, FlaskConical, Search, Send, Users, Wrench } from "lucide-react";
import type { RunStep } from "@/api/ops";
import type { ThreadRun } from "@/api/agent";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const TOOL_ICON = { component_details: Search, alternative_suppliers: Users, simulate_plan: FlaskConical, submit_plan: Send } as const;

/** A tool call in words, as the planner's steps read best. */
function said(step: RunStep): string {
  const input = step.input ?? {};
  const result = typeof step.result === "object" && step.result ? (step.result as Record<string, unknown>) : {};
  switch (step.name) {
    case "component_details":
      return `Looked up ${String(input.component)}`;
    case "alternative_suppliers":
      return `Checked alternative suppliers for ${String(input.component)}`;
    case "simulate_plan": {
      const n = Array.isArray(input.actions) ? input.actions.length : 0;
      let text = `Simulated a plan with ${n} action${n === 1 ? "" : "s"}`;
      if (result.total_cost) text += ` → ${String(result.total_cost)} (${String(result.change_vs_doing_nothing)} vs doing nothing)`;
      return text;
    }
    case "submit_plan":
      return result.accepted ? `Submitted the plan: ${String(result.total_cost)}` : "Submitted a plan; some actions were refused and sent back";
    default:
      return step.name ?? "tool";
  }
}

const json = (v: unknown) => {
  const s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  return s && s.length > 4000 ? `${s.slice(0, 4000)}…` : s;
};

/** Each step of a turn: thoughts, errors, and tool calls with their input and result. */
export function StepList({ steps, live = false }: { steps: RunStep[]; live?: boolean }) {
  return (
    <ol className="flex flex-col gap-1" aria-label="Agent steps" aria-live={live ? "polite" : undefined}>
      {steps.map((s, i) => (
        <Step key={i} step={s} />
      ))}
    </ol>
  );
}

function Step({ step }: { step: RunStep }) {
  if (step.kind === "thought") return <li className="py-0.5 pl-6 text-xs text-muted italic">{step.text}</li>;
  if (step.kind === "error")
    return (
      <li className="flex items-center gap-2 py-0.5 text-xs text-conflict">
        <CircleAlert className="size-3.5" /> {step.text}
      </li>
    );
  const Icon = TOOL_ICON[step.name as keyof typeof TOOL_ICON] ?? Wrench;
  return (
    <li className={cn("text-sm", step.error && "text-conflict")}>
      <details className="group">
        <summary className="flex cursor-pointer list-none items-start gap-2 py-0.5">
          <Icon className="mt-0.5 size-4 shrink-0 text-brand" aria-hidden />
          <span className="min-w-0 flex-1">{said(step)}</span>
          <span className="shrink-0 font-mono text-[11px] text-muted">{step.name}</span>
        </summary>
        <div className="mt-1 mb-2 ml-6 grid gap-1 text-xs">
          <span className="text-muted">Input</span>
          <pre className="max-h-48 overflow-auto rounded bg-surface-2 p-2 whitespace-pre-wrap">{json(step.input ?? {})}</pre>
          <span className="text-muted">Result</span>
          <pre className="max-h-64 overflow-auto rounded bg-surface-2 p-2 whitespace-pre-wrap">{json(step.result)}</pre>
        </div>
      </details>
    </li>
  );
}

/** A turn's steps in the detail column. */
export function Steps({ run }: { run: ThreadRun | undefined }) {
  if (!run) return <p className="p-4 text-sm text-muted">This turn is not in the thread.</p>;
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">Turn {run.id}</h2>
        <Badge className={cn(run.status === "done" && "text-ok", run.status === "failed" && "text-conflict", run.status === "running" && "text-interactive")}>{run.status}</Badge>
        {run.profile && <span className="text-xs text-muted">{run.profile}</span>}
      </div>
      {run.error && <p className="text-sm text-conflict">{run.error}</p>}
      <StepList steps={run.steps} live={run.status === "running"} />
    </div>
  );
}
