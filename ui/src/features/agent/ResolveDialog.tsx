import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useResolve } from "@/api/agent";
import type { DisruptionDetail } from "@/api/ops";
import { useAuthor } from "@/app/author";
import { ErrorNote } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label, Textarea } from "@/components/ui/input";

/** Mark resolved: a closing note, and the open tasks named (resolving does not cancel them). The outcome is recorded
 *  and the disruption moves to Reports. */
export function ResolveDialog({ detail, open, onClose }: { detail: DisruptionDetail; open: boolean; onClose: () => void }) {
  const [note, setNote] = useState("");
  const resolve = useResolve();
  const navigate = useNavigate();
  const { requireAuthor } = useAuthor();
  const d = detail.disruption;
  const openTasks = detail.tasks.filter((t) => t.status === "open" || t.status === "in_progress");
  const submit = () =>
    requireAuthor(() => resolve.mutate({ id: d.id, note: note.trim() }, { onSuccess: () => (onClose(), navigate(`/reports/d/${d.id}`)) }));
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Resolve {d.name}</DialogTitle>
          <DialogDescription>
            The outcome is recorded as it stands now, with its report, and the disruption moves to Reports. It can no longer be edited or planned.
          </DialogDescription>
        </DialogHeader>
        {openTasks.length > 0 && (
          <div className="flex flex-col gap-1 rounded-md border border-proposed/40 bg-proposed/10 px-3 py-2 text-sm">
            <span className="font-medium">
              {openTasks.length} task{openTasks.length === 1 ? " is" : "s are"} still open and will stay open:
            </span>
            <ul className="list-disc pl-5 text-xs" aria-label="Open tasks">
              {openTasks.map((t) => (
                <li key={t.id}>
                  {t.title} ({t.status.replace("_", " ")}{t.owner ? `, ${t.owner}` : ""})
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor="resolve-note">Closing note</Label>
          <Textarea id="resolve-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What ended it, what was done" />
        </div>
        {resolve.error ? <ErrorNote error={resolve.error} /> : null}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="accent" disabled={resolve.isPending} onClick={submit}>Resolve</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
