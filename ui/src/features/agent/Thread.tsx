import { Bot, ChevronDown, ChevronRight, Loader2, UserRound, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Message, ThreadRead, ThreadRun } from "@/api/agent";
import { cn, formatTime } from "@/lib/utils";
import type { DetailCrumb } from "./address";
import { BlockCard, Decision, nodeIds, QueuedMark, RichText } from "./cards";
import { StepList } from "./detail/Steps";

/** The thread: messages in order with author and time; agent answers with their text, at most one card and their
 *  steps folded; while a turn runs its steps as they happen. Without `onOpen` (read-only, in Reports) cards and node
 *  ids are plain text: decisions live in the detail column and the message box, which a read-only view leaves out. */
export function Thread({ data, onOpen }: { data: ThreadRead; onOpen?: (crumb: DetailCrumb) => void }) {
  const runs = useMemo(() => new Map(data.runs.map((r) => [r.id, r])), [data.runs]);
  const live = data.runs.filter((r) => r.status === "running");
  const end = useRef<HTMLDivElement>(null);
  const count = data.messages.length + live.reduce((n, r) => n + r.steps.length, 0);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" }); // returns a promise in recent browsers: not an effect cleanup
  }, [count]);
  const openNode = (type: string, id: string) => onOpen?.({ kind: "node", id: `${type}:${id}` });

  return (
    <ol className="flex flex-col gap-4 px-5 py-4" aria-label="Messages">
      {data.messages.map((m) => (
        <MessageItem key={m.id} message={m} run={m.run_id !== null ? runs.get(m.run_id) : undefined} onOpen={onOpen} onNode={openNode} />
      ))}
      {live.map((r) => (
        <li key={`live-${r.id}`} className="flex gap-3" aria-label="Working">
          <Avatar role="agent" />
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <span className="flex items-center gap-2 text-xs text-muted">
              <Loader2 className="size-3.5 animate-spin" /> Working · {r.steps.length} step{r.steps.length === 1 ? "" : "s"} so far
            </span>
            <StepList steps={r.steps} live />
          </div>
        </li>
      ))}
      <div ref={end} />
    </ol>
  );
}

function Avatar({ role }: { role: Message["role"] }) {
  const Icon = role === "agent" ? Bot : role === "system" ? Wrench : UserRound;
  return (
    <span className={cn("mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full", role === "agent" ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted")}>
      <Icon className="size-4" aria-hidden />
    </span>
  );
}

function MessageItem({ message: m, run, onOpen, onNode }: {
  message: Message;
  run: ThreadRun | undefined;
  onOpen?: (crumb: DetailCrumb) => void;
  onNode: (type: string, id: string) => void;
}) {
  const ids = useMemo(() => (run && onOpen ? nodeIds(run.steps) : undefined), [run, onOpen]);
  const texts = m.blocks.filter((b) => b.type === "text");
  const others = m.blocks.filter((b) => b.type !== "text");
  // an agent answer shows one card; a person's message shows its decisions as lines
  const shown = m.role === "agent" ? others.slice(0, 1) : others;
  const who = m.role === "agent" ? m.author.replace(/^agent:/, "Agent · ") : m.role === "system" ? "System" : m.author;
  return (
    <li className="flex gap-3" aria-label={`Message from ${who}`} data-role={m.role}>
      <Avatar role={m.role} />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <span className="flex items-center gap-2 text-xs text-muted">
          <span className="font-medium text-fg">{who}</span>
          <time dateTime={m.created_at}>{formatTime(m.created_at)}</time>
          {m.status === "queued" && <QueuedMark />}
        </span>
        {texts.map((b, i) => (
          <RichText key={i} text={(b as { text: string }).text} ids={ids} onNode={onNode} />
        ))}
        {shown.map((b, i) =>
          b.type === "decision" ? <Decision key={i} block={b} /> : <BlockCard key={i} block={b} onOpen={onOpen} />,
        )}
        {m.role === "agent" && run && run.steps.length > 0 && <FoldedSteps run={run} onOpen={onOpen} />}
      </div>
    </li>
  );
}

/** The turn's steps folded into one line; unfolding lists each call with its input and result. */
function FoldedSteps({ run, onOpen }: { run: ThreadRun; onOpen?: (crumb: DetailCrumb) => void }) {
  const [open, setOpen] = useState(false);
  const n = run.steps.filter((s) => s.kind === "tool").length;
  return (
    <div className="flex flex-col gap-1">
      <span className="flex items-center gap-2">
        <button type="button" className="flex cursor-pointer items-center gap-1 text-xs text-muted hover:text-fg" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {n} step{n === 1 ? "" : "s"}
        </button>
        {onOpen && (
          <button type="button" className="cursor-pointer text-xs text-interactive hover:underline" onClick={() => onOpen({ kind: "steps", id: String(run.id) })}>
            open
          </button>
        )}
      </span>
      {open && <StepList steps={run.steps} />}
    </div>
  );
}
