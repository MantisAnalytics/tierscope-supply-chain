import { CalendarClock, ChevronRight, FilePenLine, FlaskConical, Gavel, ListChecks, PencilLine, Zap, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { Block } from "@/api/agent";
import type { RunStep } from "@/api/ops";
import { cn, formatEur, formatValue } from "@/lib/utils";
import type { DetailCrumb } from "./address";

/** One line for a block, with its headline figure; clicking it opens its object in the detail column. Text and
 *  decision blocks are not cards (see `Decision`). */
export function BlockCard({ block, onOpen }: { block: Block; onOpen?: (crumb: DetailCrumb) => void }) {
  const open = (crumb: DetailCrumb) => (onOpen ? () => onOpen(crumb) : undefined);
  switch (block.type) {
    case "plan": {
      const p = block.plan;
      if (!p) return <Line Icon={ListChecks} title={`Plan ${block.ref}`} muted>removed</Line>;
      const decided = p.actions.filter((a) => a.status === "accepted" || a.status === "rejected").length;
      return (
        <Line Icon={ListChecks} title={`Plan v${p.version}`} muted={!p.current} onClick={open({ kind: "plan", id: String(p.version) })}>
          <span className="tabular">
            <span className="text-risk">{formatEur(p.do_nothing_cost)}</span> if nothing is done →{" "}
            <span className="text-ok">{formatEur(p.outcome?.total_cost)}</span>
          </span>
          <span>{p.actions.length} action{p.actions.length === 1 ? "" : "s"}</span>
          <span>{decided} decided</span>
          {!p.current && <Tag>superseded</Tag>}
          {p.current && p.stale && <Tag tone="text-proposed">stale</Tag>}
        </Line>
      );
    }
    case "edit": {
      const e = block.edit;
      const first = e?.changes[0];
      return (
        <Line Icon={PencilLine} title="Edit" onClick={open({ kind: "edit", id: String(block.ref) })}>
          {first ? (
            <span className="min-w-0 truncate">
              {first.edit} <span className="tabular">({formatValue(first.old)} → {formatValue(first.new)})</span>
              {e!.changes.length > 1 && ` +${e!.changes.length - 1} more`}
            </span>
          ) : (
            <span>edit block {block.ref}</span>
          )}
          {e && <Tag tone={e.status === "applied" ? "text-ok" : e.status === "proposed" ? "text-proposed" : undefined}>{e.status}</Tag>}
        </Line>
      );
    }
    case "draft":
      return (
        <Line Icon={FilePenLine} title="Draft disruption" onClick={open({ kind: "draft", id: String(block.ref) })}>
          <span className="min-w-0 truncate">{block.disruption?.name ?? `disruption ${block.ref}`}</span>
          {block.disruption && <Tag tone={block.disruption.status === "draft" ? "text-proposed" : undefined}>{block.disruption.status}</Tag>}
        </Line>
      );
    case "simulation":
      return <Line Icon={FlaskConical} title="Simulation" onClick={open({ kind: "sim", id: String(block.ref) })}>simulation {block.ref}</Line>;
    case "event": {
      const ev = block.event;
      const changed = ev?.payload.changed as string[] | undefined;
      return (
        <Line Icon={Zap} title="Event">
          <span className="min-w-0 truncate">
            {ev ? `${ev.type} from ${ev.source}` : `event ${block.ref}`}
            {changed?.length ? ` · changed ${changed.join(", ")}` : ""}
          </span>
        </Line>
      );
    }
    case "decision":
      return <Decision block={block} />;
    default:
      return null;
  }
}

/** A decision a person took, as one line: "accepted action Spot buy …: “comment”". */
export function Decision({ block, author }: { block: Extract<Block, { type: "decision" }>; author?: string }) {
  return (
    <Line Icon={Gavel} title="Decision">
      <span className="min-w-0 truncate">
        {author && <span className="font-medium">{author} </span>}
        {block.decision} {block.kind}
        {block.what ? ` ${block.what}` : ` ${block.ref}`}
        {block.comment && <span className="text-fg"> · “{block.comment}”</span>}
      </span>
    </Line>
  );
}

function Tag({ children, tone }: { children: ReactNode; tone?: string }) {
  return <span className={cn("rounded border px-1 text-[11px] leading-4", tone ?? "text-muted")}>{children}</span>;
}

function Line({ Icon, title, children, onClick, muted }: { Icon: LucideIcon; title: string; children: ReactNode; onClick?: () => void; muted?: boolean }) {
  const body = (
    <>
      <Icon className="size-4 shrink-0 text-brand" aria-hidden />
      <span className="shrink-0 font-medium">{title}</span>
      <span className="flex min-w-0 flex-1 items-center gap-2 text-xs text-muted">{children}</span>
      {onClick && <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden />}
    </>
  );
  const cls = cn("flex w-full items-center gap-2 rounded-lg border bg-surface px-3 py-2 text-left text-sm", muted && "opacity-70");
  return onClick ? (
    <button type="button" className={cn(cls, "cursor-pointer hover:border-interactive/50 hover:bg-surface-2")} onClick={onClick} data-card={title}>
      {body}
    </button>
  ) : (
    <div className={cls} data-card={title}>{body}</div>
  );
}

// --- text with node links --------------------------------------------------------------------------------------

const TYPED_KEYS: Record<string, string> = {
  component: "Component", components: "Component", supplier: "Supplier", suppliers: "Supplier", site: "Site", sites: "Site",
  assembly: "Assembly", assemblies: "Assembly",
};

/** Node ids the turn's tools returned or were asked about, with their type: `{type, id}` objects and values under keys
 *  such as `supplier` or `components`. Only these become links, so random words are never linked. */
export function nodeIds(steps: RunStep[]): Map<string, string> {
  const out = new Map<string, string>();
  const add = (type: string, value: unknown) => {
    if (typeof value === "string" && value.length >= 3 && !out.has(value)) out.set(value, type);
    else if (Array.isArray(value)) value.forEach((v) => add(type, v));
  };
  const walk = (value: unknown, depth: number) => {
    if (depth > 6 || value === null || typeof value !== "object") return;
    if (Array.isArray(value)) return value.forEach((v) => walk(v, depth + 1));
    const o = value as Record<string, unknown>;
    if (typeof o.type === "string" && typeof o.id === "string" && /^[A-Z]/.test(o.type)) add(o.type, o.id);
    for (const [k, v] of Object.entries(o)) {
      if (TYPED_KEYS[k]) add(TYPED_KEYS[k], v);
      walk(v, depth + 1);
    }
  };
  for (const s of steps) if (s.kind === "tool") (walk(s.input, 0), walk(s.result, 0));
  return out;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function linked(text: string, ids: Map<string, string>, onNode: (type: string, id: string) => void): ReactNode[] {
  if (ids.size === 0) return [text];
  const pattern = new RegExp(`(?<![\\w-])(${[...ids.keys()].sort((a, b) => b.length - a.length).map(escape).join("|")})(?![\\w-])`, "g");
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(pattern)) {
    out.push(text.slice(at, m.index));
    const id = m[1];
    out.push(
      <button key={`${m.index}-${id}`} type="button" className="cursor-pointer font-medium text-interactive hover:underline" onClick={() => onNode(ids.get(id)!, id)}>
        {id}
      </button>,
    );
    at = m.index! + id.length;
  }
  out.push(text.slice(at));
  return out;
}

const ITEM = /^\s*(?:[-*•]|\d+[.)])\s+/;

/** Agent text as paragraphs and lists; node ids from the turn's tools are links that open the node. */
export function RichText({ text, ids = new Map(), onNode }: { text: string; ids?: Map<string, string>; onNode: (type: string, id: string) => void }) {
  const blocks = text.trim().split(/\n\s*\n/);
  return (
    <div className="flex flex-col gap-2 text-sm leading-relaxed">
      {blocks.map((b, i) => {
        const lines = b.split("\n");
        if (lines.every((l) => ITEM.test(l))) {
          const ordered = /^\s*\d/.test(lines[0]);
          const List = ordered ? "ol" : "ul";
          return (
            <List key={i} className={cn("flex flex-col gap-0.5 pl-5", ordered ? "list-decimal" : "list-disc")}>
              {lines.map((l, j) => (
                <li key={j}>{linked(l.replace(ITEM, ""), ids, onNode)}</li>
              ))}
            </List>
          );
        }
        return (
          <p key={i} className="whitespace-pre-line">
            {linked(b, ids, onNode)}
          </p>
        );
      })}
    </div>
  );
}

export function QueuedMark() {
  return (
    <span className="inline-flex items-center gap-1 rounded border px-1 text-[11px] leading-4 text-muted">
      <CalendarClock className="size-3" aria-hidden /> queued
    </span>
  );
}
