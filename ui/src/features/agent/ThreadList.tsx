import { ChevronDown, ChevronRight, MessageSquare, Plus, Siren } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { ThreadRow } from "@/api/agent";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn, formatTime } from "@/lib/utils";

const WEEK = 7 * 24 * 3600 * 1000;
const time = (t: ThreadRow) => Date.parse(t.last?.created_at ?? t.updated_at);

/** Disruptions waiting for a person first (then the most recently active), and conversations newest first with those
 *  older than a week folded. */
export function sortThreads(rows: ThreadRow[], now = Date.now()) {
  const disruptions = rows
    .filter((t) => t.disruption_id !== null)
    .sort((a, b) => Number(!!b.needs) - Number(!!a.needs) || time(b) - time(a));
  const conversations = rows.filter((t) => t.disruption_id === null).sort((a, b) => time(b) - time(a));
  return {
    disruptions,
    recent: conversations.filter((t) => now - time(t) <= WEEK),
    older: conversations.filter((t) => now - time(t) > WEEK),
  };
}

export function ThreadList({ threads, current }: { threads: ThreadRow[]; current?: number }) {
  const [q, setQ] = useState("");
  const [showOlder, setShowOlder] = useState(false);
  const needle = q.trim().toLowerCase();
  const { disruptions, recent, older } = sortThreads(threads.filter((t) => !needle || t.title.toLowerCase().includes(needle)));
  return (
    <nav aria-label="Threads" className="flex min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b p-3">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search threads" aria-label="Search threads" className="h-8 text-[13px]" />
        <Button asChild size="sm" variant="outline">
          <Link to="/agent">
            <Plus /> New
          </Link>
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <Section title="Disruptions" count={disruptions.length}>
          {disruptions.length === 0 ? (
            <p className="px-4 pb-2 text-xs text-muted">{needle ? "No match." : "No open disruptions."}</p>
          ) : (
            disruptions.map((t) => <Item key={t.id} thread={t} current={t.id === current} />)
          )}
        </Section>
        <Section title="Conversations" count={recent.length + older.length}>
          {recent.length + older.length === 0 && <p className="px-4 pb-2 text-xs text-muted">{needle ? "No match." : "None yet."}</p>}
          {recent.map((t) => (
            <Item key={t.id} thread={t} current={t.id === current} />
          ))}
          {older.length > 0 && (
            <>
              <button type="button" className="flex w-full cursor-pointer items-center gap-1 px-4 py-1.5 text-xs text-muted hover:text-fg" aria-expanded={showOlder} onClick={() => setShowOlder((s) => !s)}>
                {showOlder ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />} Older ({older.length})
              </button>
              {showOlder && older.map((t) => <Item key={t.id} thread={t} current={t.id === current} />)}
            </>
          )}
        </Section>
      </div>
    </nav>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="pt-3">
      <h2 className="px-4 pb-1.5 text-xs font-medium text-muted">
        {title} <span className="tabular">({count})</span>
      </h2>
      <ul>{children}</ul>
    </section>
  );
}

function Item({ thread: t, current }: { thread: ThreadRow; current: boolean }) {
  const Icon = t.disruption_id !== null ? Siren : MessageSquare;
  return (
    <li>
      <Link
        to={`/agent/t/${t.id}`}
        aria-current={current ? "page" : undefined}
        className={cn("flex items-start gap-2.5 border-b px-4 py-2 hover:bg-surface-2", current && "bg-interactive-soft hover:bg-interactive-soft")}
      >
        <Icon className={cn("mt-0.5 size-4 shrink-0", t.needs ? "text-risk" : "text-muted")} aria-hidden />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-sm leading-snug font-medium">{t.title}</span>
            {t.disruption_status === "draft" && <span className="shrink-0 rounded border border-proposed/50 px-1 text-[11px] leading-4 text-proposed">draft</span>}
          </span>
          {t.needs && <span className="w-fit rounded bg-risk-soft px-1.5 text-[11px] leading-4 font-medium text-risk" data-needs={t.needs}>{t.needs}</span>}
          {t.last && <span className="truncate text-xs text-muted">{t.last.text}</span>}
          <span className="text-[11px] text-muted">{formatTime(t.last?.created_at ?? t.updated_at)}</span>
        </span>
      </Link>
    </li>
  );
}
