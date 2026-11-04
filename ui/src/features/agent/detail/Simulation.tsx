import type { ThreadRead } from "@/api/agent";

/** A simulation block: the agent ran a what-if; its plan and outcome are in the step that produced it. */
export function Simulation({ id, data }: { id: string; data: ThreadRead }) {
  const step = data.runs.flatMap((r) => r.steps).find((s) => s.name === "simulate_plan" && String((s.result as { id?: unknown } | undefined)?.id) === id);
  return (
    <div className="flex flex-col gap-2 p-4" aria-label={`Simulation ${id}`}>
      <h2 className="text-base font-semibold">Simulation {id}</h2>
      {step ? (
        <pre className="max-h-[60vh] overflow-auto rounded bg-surface-2 p-2 text-xs whitespace-pre-wrap">{JSON.stringify({ input: step.input, result: step.result }, null, 2)}</pre>
      ) : (
        <p className="text-sm text-muted">The simulations the agent ran are listed with each turn's steps.</p>
      )}
    </div>
  );
}
