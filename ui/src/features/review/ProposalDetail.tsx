import { Check, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import type { FactView, ProposalDetail as Detail } from "@/api/client";
import { useAccept, useProposal, useReject } from "@/api/queries";
import { useAuthor } from "@/app/author";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { ErrorNote, Loading } from "@/components/States";
import { StatusBadge } from "@/components/StatusBadge";
import { VirtualList } from "@/components/VirtualList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { formatTime, formatValue } from "@/lib/utils";
import { DependencyHint } from "./DependencyHint";
import { PreviewTable } from "./PreviewTable";
import { ResolutionPicker, type Resolution } from "./ResolutionPicker";

const FACT_COLUMNS = "grid grid-cols-[28%_22%_18%_1fr] gap-2";

function FactsTable({ facts, pending }: { facts: FactView[]; pending: boolean }) {
  return (
    <div role="table" aria-label="Facts" className="text-sm">
      <div role="rowgroup">
        <div role="row" className={`${FACT_COLUMNS} border-b py-1.5 text-xs text-muted`}>
          <span role="columnheader">Item</span>
          <span role="columnheader">Value</span>
          <span role="columnheader">{pending ? "Replaces" : ""}</span>
          <span role="columnheader">Source</span>
        </div>
      </div>
      <VirtualList
        label="Fact rows"
        role="rowgroup"
        itemRole="presentation"
        className="max-h-[60vh]"
        items={facts}
        estimate={56}
        getKey={(_, i) => i}
        render={(f) => (
          <div role="row" className={`${FACT_COLUMNS} border-b py-1.5`}>
            <span role="cell" className="break-words">
              {f.item}
              {f.field && <span className="block text-xs text-muted">{f.field}</span>}
            </span>
            <span role="cell" className="break-words">
              {f.kind === "field" ? formatValue(f.value) : <span className="text-muted">{f.kind}</span>}
              <span className="block"><ConfidenceBar value={f.confidence} /></span>
            </span>
            <span role="cell" className="break-words text-muted">{pending && f.kind === "field" ? formatValue(f.current) : ""}</span>
            <span role="cell" className="min-w-0 text-xs break-all">
              <span className="block text-muted">{f.source}</span>
              {f.quote && <span className="mt-1 block border-l-2 border-lavender pl-2 italic">“{f.quote}”</span>}
            </span>
          </div>
        )}
      />
    </div>
  );
}

function SpecContext({ spec }: { spec: Record<string, unknown> }) {
  const name = String(spec.path ?? "").split("/").pop()?.replace(/\.json$/, "");
  return (
    <div className="rounded-lg bg-surface-2 p-3 text-sm">
      <div className="flex items-center gap-2 text-xs text-muted">
        Mapping spec column
        {name && <Link to={`/data/specs/${encodeURIComponent(name)}`} className="text-interactive hover:underline">open spec</Link>}
      </div>
      <div className="font-medium">
        {String(spec.column ?? "keys")} <span className="font-normal text-muted">in region {String(spec.region ?? "")}</span>
        {typeof spec.target === "string" && <span className="font-normal"> → {spec.target}</span>}
      </div>
      {typeof spec.rationale === "string" && <p className="mt-1 text-muted">{spec.rationale}</p>}
    </div>
  );
}

export function ProposalDetail({ id, onDecided }: { id: number; onDecided?: (id: number) => void }) {
  const [samples, setSamples] = useState(5);
  const { data, error, isLoading } = useProposal(id, samples);
  const accept = useAccept();
  const reject = useReject();
  const { requireAuthor } = useAuthor();
  const [comment, setComment] = useState("");
  const [resolution, setResolution] = useState<Resolution>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  useEffect(() => {
    setSamples(5);
    setComment("");
    setResolution(null);
    setActionError(null);
  }, [id]);

  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorNote error={error ?? "not found"} className="m-4" />;
  const p: Detail = data;
  const pending = p.status === "pending";
  const isResolution = p.kind === "resolution";
  const mentionType = isResolution ? String(p.group_key[3] ?? "node") : "";

  const decide = (kind: "accept" | "reject") =>
    requireAuthor(async () => {
      setActionError(null);
      try {
        if (kind === "accept") {
          await accept.mutateAsync({ id, comment: comment || undefined, ...(resolution ?? {}) });
          toast.success(`Proposal #${id} accepted`);
        } else {
          await reject.mutateAsync({ id, comment: comment || undefined });
          toast.success(`Proposal #${id} rejected`);
        }
        onDecided?.(id);
      } catch (e) {
        setActionError(e as Error);
      }
    });

  return (
    <article className="flex flex-col gap-5 p-5" aria-label={`Proposal ${id}`}>
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="font-semibold text-fg">#{p.id}</span>
          <Badge className="border-border">{p.kind}</Badge>
          <StatusBadge status={p.status} />
          <span>{p.count} facts</span>
          <span>created {formatTime(p.created_at)}</span>
          {p.decided_by && <span>decided by {p.decided_by}{p.decided_at ? `, ${formatTime(p.decided_at)}` : ""}</span>}
        </div>
        <h1 className="text-lg leading-snug font-semibold">{p.summary}</h1>
        {p.targets.length > 0 && <div className="flex flex-wrap gap-1">{p.targets.map((t) => <Badge key={t} className="border-border text-muted">{t}</Badge>)}</div>}
        {p.comment && <p className="text-sm text-muted">Comment: {p.comment}</p>}
        {p.notes.length > 0 && <ul className="list-disc pl-5 text-sm text-muted">{p.notes.map((n, i) => <li key={i}>{typeof n === "string" ? n : JSON.stringify(n)}</li>)}</ul>}
      </header>

      {pending && p.impact && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">Exposure preview</h2>
          <DependencyHint dependsOn={p.impact.depends_on} className="rounded-md border border-proposed/40 bg-proposed/10 px-3 py-2 text-sm" />
          <PreviewTable impact={p.impact} />
        </section>
      )}

      {p.spec && <SpecContext spec={p.spec} />}

      {isResolution && pending && (
        <ResolutionPicker mention={p.mention ?? ""} candidates={p.candidates ?? []} type={mentionType} value={resolution} onChange={setResolution} />
      )}

      <section className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">{p.facts.length < p.count ? `Sample of ${p.facts.length} of ${p.count} facts` : `All ${p.count} facts`}</h2>
          {p.facts.length < p.count && (
            <Button size="xs" variant="outline" onClick={() => setSamples(p.count)}>Show all {p.count}</Button>
          )}
        </div>
        <FactsTable facts={p.facts} pending={pending} />
      </section>

      {pending && (
        <section className="sticky bottom-0 flex flex-col gap-2 border-t bg-surface pt-3 pb-1">
          <Label htmlFor="decision-comment">Comment (optional)</Label>
          <Textarea id="decision-comment" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Why you accept or reject" />
          {actionError && <ErrorNote error={actionError} />}
          <div className="flex gap-2">
            <Button variant="accent" onClick={() => decide("accept")} disabled={accept.isPending || (isResolution && !resolution)}>
              <Check /> Accept
            </Button>
            <Button variant="outline" onClick={() => decide("reject")} disabled={reject.isPending}>
              <X /> Reject
            </Button>
          </div>
        </section>
      )}
    </article>
  );
}
