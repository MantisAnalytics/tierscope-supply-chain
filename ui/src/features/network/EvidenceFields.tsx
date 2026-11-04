import { Paperclip } from "lucide-react";
import { uploadEvidence } from "@/api/queries";
import { Input, Label, Textarea } from "@/components/ui/input";

export interface EvidenceDraft {
  file?: File | null;
  page?: string;
  quote?: string;
  url?: string;
  comment?: string;
}

/** The evidence an edit carries: an uploaded file (with page and quote), a link, or a comment. */
export async function toEvidence(draft: EvidenceDraft): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {};
  if (draft.file) out.id = (await uploadEvidence(draft.file)).id;
  if (draft.page?.trim()) out.page = Number(draft.page);
  if (draft.quote?.trim()) out.quote = draft.quote.trim();
  if (draft.url?.trim()) out.url = draft.url.trim();
  if (draft.comment?.trim()) out.comment = draft.comment.trim();
  return out;
}

export function EvidenceFields({ value, onChange }: { value: EvidenceDraft; onChange: (v: EvidenceDraft) => void }) {
  const set = (patch: Partial<EvidenceDraft>) => onChange({ ...value, ...patch });
  return (
    <fieldset className="grid gap-2 rounded-lg border p-3">
      <legend className="px-1 text-xs font-medium text-muted">Evidence (optional)</legend>
      <div className="grid gap-1">
        <Label htmlFor="ev-comment">Comment</Label>
        <Input id="ev-comment" value={value.comment ?? ""} onChange={(e) => set({ comment: e.target.value })} placeholder="e.g. buyer call 29.10" />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="ev-url">Link</Label>
        <Input id="ev-url" type="url" value={value.url ?? ""} onChange={(e) => set({ url: e.target.value })} placeholder="https://…" />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="ev-file" className="flex items-center gap-1"><Paperclip className="size-3" /> File</Label>
        <Input id="ev-file" type="file" className="pt-1.5" onChange={(e) => set({ file: e.target.files?.[0] ?? null })} />
      </div>
      {value.file && (
        <div className="grid grid-cols-[6rem_1fr] gap-2">
          <div className="grid gap-1">
            <Label htmlFor="ev-page">Page</Label>
            <Input id="ev-page" type="number" min={1} value={value.page ?? ""} onChange={(e) => set({ page: e.target.value })} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ev-quote">Quote</Label>
            <Textarea id="ev-quote" className="min-h-9" value={value.quote ?? ""} onChange={(e) => set({ quote: e.target.value })} />
          </div>
        </div>
      )}
    </fieldset>
  );
}
