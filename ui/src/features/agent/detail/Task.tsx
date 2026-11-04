import { useState } from "react";
import { useUpdateTask, type Task as TaskRow } from "@/api/ops";
import { useAuthor } from "@/app/author";
import { ErrorNote } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { formatTime } from "@/lib/utils";

const STATUSES: TaskRow["status"][] = ["open", "in_progress", "done", "cancelled"];

/** A task with its status, owner, due week and notes, all editable. */
export function Task({ task, overdue = false, readOnly = false }: { task: TaskRow; overdue?: boolean; readOnly?: boolean }) {
  const update = useUpdateTask();
  const { requireAuthor } = useAuthor();
  const [status, setStatus] = useState(task.status);
  const [owner, setOwner] = useState(task.owner ?? "");
  const [due, setDue] = useState(task.due_week === null ? "" : String(task.due_week));
  const [notes, setNotes] = useState(task.notes);
  const changed = status !== task.status || owner !== (task.owner ?? "") || due !== (task.due_week === null ? "" : String(task.due_week)) || notes !== task.notes;
  const save = () =>
    requireAuthor(() => update.mutate({ id: task.id, status, owner: owner.trim() || null, due_week: due === "" ? null : Number(due), notes }));
  return (
    <div className="flex flex-col gap-3 p-4" aria-label={`Task ${task.title}`}>
      <header className="flex flex-col gap-0.5">
        <h2 className="text-base font-semibold">{task.title}</h2>
        <span className="text-xs text-muted">
          {overdue && <span className="font-medium text-risk">overdue · </span>}
          {task.updated_by ? `changed by ${task.updated_by} · ${formatTime(task.updated_at)}` : `created ${formatTime(task.created_at)}`}
        </span>
      </header>
      <div className="grid grid-cols-3 gap-2">
        <div className="grid gap-1">
          <Label htmlFor="task-status">Status</Label>
          <Select id="task-status" value={status} disabled={readOnly} onChange={(e) => setStatus(e.target.value as TaskRow["status"])}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s.replace("_", " ")}</option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="task-owner">Owner</Label>
          <Input id="task-owner" value={owner} disabled={readOnly} onChange={(e) => setOwner(e.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="task-due">Due week</Label>
          <Input id="task-due" type="number" min={0} value={due} disabled={readOnly} onChange={(e) => setDue(e.target.value)} />
        </div>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="task-notes">Notes</Label>
        <Textarea id="task-notes" rows={4} value={notes} disabled={readOnly} onChange={(e) => setNotes(e.target.value)} />
      </div>
      {!readOnly && (
        <Button size="sm" variant="accent" className="w-fit" disabled={!changed || update.isPending} onClick={save}>
          Save task
        </Button>
      )}
      {update.error ? <ErrorNote error={update.error} /> : null}
    </div>
  );
}
