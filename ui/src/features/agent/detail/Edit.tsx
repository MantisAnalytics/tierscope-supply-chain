import { Check, X } from "lucide-react";
import { useState } from "react";
import { useApplyEdit, useDismissEdit, type EditBlock } from "@/api/agent";
import { useAuthor } from "@/app/author";
import { ErrorNote } from "@/components/States";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatTime, formatValue } from "@/lib/utils";

/** An edit block: every change with its old and new value, the exposure preview, and Apply or Dismiss. */
export function Edit({ block, readOnly = false }: { block: EditBlock; readOnly?: boolean }) {
  const apply = useApplyEdit();
  const dismiss = useDismissEdit();
  const { requireAuthor } = useAuthor();
  const [comment, setComment] = useState("");
  const preview = block.preview ?? {};
  return (
    <div className="flex flex-col gap-4 p-4" aria-label={`Edit ${block.id}`}>
      <header className="flex items-center gap-2">
        <h2 className="text-base font-semibold">Edit {block.id}</h2>
        <Badge className={block.status === "applied" ? "text-ok" : block.status === "proposed" ? "text-proposed" : "text-muted"}>{block.status}</Badge>
        {block.decided_by && (
          <span className="text-xs text-muted">
            by {block.decided_by}
            {block.decided_at && ` · ${formatTime(block.decided_at)}`}
          </span>
        )}
      </header>
      {block.reason && <p className="text-sm">{block.reason}</p>}
      <table className="w-full text-sm" aria-label="Changes">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1 font-medium">Change</th>
            <th className="py-1 font-medium">Now</th>
            <th className="py-1 font-medium">New</th>
          </tr>
        </thead>
        <tbody>
          {block.changes.map((c, i) => (
            <tr key={i} className="border-t align-top">
              <td className="py-1.5 pr-2">{c.edit}</td>
              <td className="py-1.5 pr-2 text-muted tabular">{formatValue(c.old)}</td>
              <td className="py-1.5 font-medium tabular">{formatValue(c.new)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(preview).length > 0 && (
        <section aria-label="Exposure preview" className="flex flex-col gap-1">
          <h3 className="text-xs font-medium text-muted">Exposure preview</h3>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
            {Object.entries(preview).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
                <dd className="tabular">{typeof v === "object" ? JSON.stringify(v) : formatValue(v)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {block.status === "proposed" && !readOnly && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Button size="sm" variant="accent" disabled={apply.isPending} onClick={() => requireAuthor(() => apply.mutate(block.id))}>
              <Check /> Apply
            </Button>
            <Input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Why not (optional)" className="h-8 text-[13px]" aria-label="Dismiss comment" />
            <Button size="sm" variant="ghost" disabled={dismiss.isPending} onClick={() => requireAuthor(() => dismiss.mutate({ id: block.id, comment: comment.trim() || undefined }))}>
              <X /> Dismiss
            </Button>
          </div>
          {apply.error ? <ErrorNote error={apply.error} /> : null}
          {dismiss.error ? <ErrorNote error={dismiss.error} /> : null}
        </div>
      )}
    </div>
  );
}
