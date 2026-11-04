import { Check, X } from "lucide-react";
import type { DraftDisruption } from "@/api/agent";
import { useConfirmDraft, useDiscardDraft, useOpsMeta, useUpdateDisruption, type Disruption } from "@/api/ops";
import { useAuthor } from "@/app/author";
import { ErrorNote, Loading } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DisruptionForm } from "./DisruptionForm";

/** A draft disruption: its warnings, the form to change it (saving re-plans it), and Confirm or Discard. */
export function Draft({ draft, readOnly = false }: { draft: DraftDisruption; readOnly?: boolean }) {
  const { data: meta } = useOpsMeta();
  const update = useUpdateDisruption();
  const confirm = useConfirmDraft();
  const discard = useDiscardDraft();
  const { requireAuthor } = useAuthor();
  const isDraft = draft.status === "draft";
  const initial = {
    name: draft.name, type: draft.type, start_week: draft.start_week, duration_weeks: draft.duration_weeks, description: draft.description,
    params: draft.params, stock_cover_weeks: draft.stock_cover_weeks, horizon_weeks: draft.horizon_weeks, source: draft.source,
  } as Disruption;
  return (
    <div className="flex flex-col gap-4 p-4" aria-label={`Draft ${draft.name}`}>
      <header className="flex items-center gap-2">
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{draft.name}</h2>
        <Badge className={isDraft ? "text-proposed" : "text-muted"}>{draft.status}</Badge>
      </header>
      {isDraft && !readOnly && (
        <div className="flex items-center gap-2">
          <Button size="sm" variant="accent" disabled={confirm.isPending} onClick={() => requireAuthor(() => confirm.mutate(draft.id))}>
            <Check /> Confirm
          </Button>
          <Button size="sm" variant="ghost" disabled={discard.isPending} onClick={() => requireAuthor(() => discard.mutate(draft.id))}>
            <X /> Discard
          </Button>
        </div>
      )}
      {(confirm.error || discard.error) ? <ErrorNote error={confirm.error ?? discard.error} /> : null}
      {draft.warnings && draft.warnings.length > 0 && (
        <ul className="rounded-md border border-proposed/40 bg-proposed/10 px-3 py-2 text-xs text-proposed" aria-label="Warnings">
          {draft.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {readOnly || !isDraft ? (
        draft.description && <p className="text-sm text-muted">{draft.description}</p>
      ) : !meta ? (
        <Loading />
      ) : (
        <>
          {update.error ? <ErrorNote error={update.error} /> : null}
          <DisruptionForm meta={meta} initial={initial} submitLabel="Save changes" busy={update.isPending}
            onSubmit={(next) => requireAuthor(() => update.mutate({ id: draft.id, d: next }))} />
        </>
      )}
    </div>
  );
}
