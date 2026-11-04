import { Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useEdits, type Edit } from "@/api/queries";
import { parseValue, useSchema } from "@/api/graphSchema";
import { useAuthor } from "@/app/author";
import { NodePicker } from "@/components/NodePicker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input, Label, Select } from "@/components/ui/input";
import { formatValue, humanize } from "@/lib/utils";
import { EvidenceFields, toEvidence, type EvidenceDraft } from "./EvidenceFields";

export type EditIntent =
  | { kind: "set_field"; type: string; key: unknown[]; label: string; field?: string; current?: unknown }
  | { kind: "create_node"; type?: string }
  | { kind: "add_edge"; from: { type: string; id: string } }
  | { kind: "delete"; type: string; key: unknown[]; label: string; field?: string }
  | { kind: "merge"; type: string; id: string; label: string };

const TITLES: Record<EditIntent["kind"], string> = {
  set_field: "Change a value",
  create_node: "Create a node",
  add_edge: "Add an edge",
  delete: "Delete",
  merge: "Merge into another node",
};

/** One edit with optional evidence; the API's refusal is shown here and nothing else changes. */
export function EditDialog({ intent, onClose, onDone }: {
  intent: EditIntent | null;
  onClose: () => void;
  onDone?: (edit: Edit) => void;
}) {
  return (
    <Dialog open={!!intent} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] max-w-xl overflow-auto">
        {intent && <EditForm key={JSON.stringify(intent)} intent={intent} onClose={onClose} onDone={onDone} />}
      </DialogContent>
    </Dialog>
  );
}

function EditForm({ intent, onClose, onDone }: { intent: EditIntent; onClose: () => void; onDone?: (edit: Edit) => void }) {
  const { data: schema } = useSchema();
  const { requireAuthor } = useAuthor();
  const edits = useEdits();
  const [evidence, setEvidence] = useState<EvidenceDraft>({});
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [busy, setBusy] = useState(false);

  // form state shared by the kinds
  const [field, setField] = useState(intent.kind === "set_field" ? (intent.field ?? "") : "");
  const [value, setValue] = useState(intent.kind === "set_field" && intent.current !== undefined ? formatValue(intent.current).replace(/^—$/, "") : "");
  const [nodeType, setNodeType] = useState(intent.kind === "create_node" ? (intent.type ?? "Supplier") : "");
  const [nodeId, setNodeId] = useState("");
  const [rows, setRows] = useState<{ field: string; value: string }[]>([{ field: "", value: "" }]);
  const [relation, setRelation] = useState("");
  const [other, setOther] = useState("");
  const [identity, setIdentity] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [into, setInto] = useState("");

  if (!schema) return null;

  const fieldsOf = (type: string) =>
    schema.entities[type]?.fields ?? schema.relations[type]?.fields ?? {};

  const build = (): Edit => {
    switch (intent.kind) {
      case "set_field":
        return { op: "set_field", type: intent.type, key: intent.key, field, value: parseValue(value, fieldsOf(intent.type)[field]) };
      case "create_node": {
        const types = fieldsOf(nodeType);
        const fields = Object.fromEntries(rows.filter((r) => r.field && r.value.trim()).map((r) => [r.field, parseValue(r.value, types[r.field])]));
        return { op: "create_node", type: nodeType, id: nodeId.trim(), fields };
      }
      case "add_edge": {
        const rel = schema.relations[relation];
        const outgoing = rel?.from === intent.from.type;
        const ends = outgoing ? [intent.from.id, other] : [other, intent.from.id];
        const ids = Object.keys(rel?.identity ?? {}).map((k) => identity[k]?.trim() || null);
        return { op: "add_edge", type: relation, key: [...ends, ...ids] };
      }
      case "delete":
        return { op: "delete", type: intent.type, key: intent.key, field: intent.field ?? null, reason: reason.trim() || null };
      case "merge":
        return { op: "merge", type: intent.type, from: intent.id, into };
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    requireAuthor(async () => {
      setBusy(true);
      setError(null);
      try {
        const edit = build();
        const ev = await toEvidence(evidence);
        const { comment, id, ...rest } = ev;
        if (Object.keys(rest).length) edit.evidence = rest;
        await edits.mutateAsync({ edits: [edit], evidence_id: id as string | undefined, comment: comment as string | undefined });
        toast.success("Change saved");
        onDone?.(edit);
        onClose();
      } catch (err) {
        setError(err as Error);
      } finally {
        setBusy(false);
      }
    });
  };

  let body: ReactNode = null;
  let description = "";
  if (intent.kind === "set_field") {
    description = intent.label;
    const fields = fieldsOf(intent.type);
    body = (
      <>
        <div className="grid gap-1">
          <Label htmlFor="edit-field">Field</Label>
          <Select id="edit-field" value={field} onChange={(e) => setField(e.target.value)} disabled={!!intent.field}>
            <option value="">Choose a field</option>
            {Object.entries(fields).map(([name, t]) => (
              <option key={name} value={name}>{humanize(name)} ({t})</option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="edit-value">New value</Label>
          <Input id="edit-value" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        </div>
      </>
    );
  } else if (intent.kind === "create_node") {
    const fields = fieldsOf(nodeType);
    body = (
      <>
        <div className="grid grid-cols-2 gap-2">
          <div className="grid gap-1">
            <Label htmlFor="node-type">Type</Label>
            <Select id="node-type" value={nodeType} onChange={(e) => setNodeType(e.target.value)}>
              {Object.keys(schema.entities).map((t) => <option key={t}>{t}</option>)}
            </Select>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="node-id">Id</Label>
            <Input id="node-id" value={nodeId} onChange={(e) => setNodeId(e.target.value)} placeholder="e.g. S900001" />
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label>Fields</Label>
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
              <Select aria-label={`Field ${i + 1}`} value={r.field} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)))}>
                <option value="">Field</option>
                {Object.keys(fields).map((f) => <option key={f} value={f}>{humanize(f)}</option>)}
              </Select>
              <Input aria-label={`Value ${i + 1}`} value={r.value} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
              <Button size="icon" variant="ghost" aria-label="Remove field" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 /></Button>
            </div>
          ))}
          <Button size="sm" variant="outline" className="justify-self-start" onClick={() => setRows([...rows, { field: "", value: "" }])}><Plus /> Field</Button>
        </div>
      </>
    );
  } else if (intent.kind === "add_edge") {
    description = `From ${intent.from.type} ${intent.from.id}`;
    const options = Object.entries(schema.relations).filter(([, r]) => r.from === intent.from.type || r.to === intent.from.type);
    const rel = schema.relations[relation];
    const otherType = rel ? (rel.from === intent.from.type ? rel.to : rel.from) : undefined;
    body = (
      <>
        <div className="grid gap-1">
          <Label htmlFor="edge-type">Relation</Label>
          <Select id="edge-type" value={relation} onChange={(e) => setRelation(e.target.value)}>
            <option value="">Choose a relation</option>
            {options.map(([name, r]) => (
              <option key={name} value={name}>{name} ({r.from} → {r.to})</option>
            ))}
          </Select>
        </div>
        {rel && (
          <div className="grid gap-1">
            <Label htmlFor="edge-other">{rel.from === intent.from.type ? "To" : "From"} {otherType}</Label>
            <NodePicker id="edge-other" type={otherType} value={other} onChange={setOther} />
          </div>
        )}
        {rel && Object.entries(rel.identity).map(([name, ref]) => (
          <div key={name} className="grid gap-1">
            <Label htmlFor={`edge-${name}`}>{humanize(name)} ({ref})</Label>
            {schema.entities[ref] ? (
              <NodePicker id={`edge-${name}`} type={ref} value={identity[name] ?? ""} onChange={(v) => setIdentity({ ...identity, [name]: v })} />
            ) : (
              <Input id={`edge-${name}`} value={identity[name] ?? ""} onChange={(e) => setIdentity({ ...identity, [name]: e.target.value })} />
            )}
          </div>
        ))}
      </>
    );
  } else if (intent.kind === "delete") {
    description = intent.field ? `The value of ${humanize(intent.field)} on ${intent.label}` : intent.label;
    body = (
      <div className="grid gap-1">
        <Label htmlFor="delete-reason">Reason</Label>
        <Input id="delete-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this is wrong" />
        <p className="text-xs text-muted">Deletion is recorded; the same source will not bring it back, new evidence becomes a proposal.</p>
      </div>
    );
  } else if (intent.kind === "merge") {
    description = `${intent.label} disappears; its sources, fields and edges move to the node you choose.`;
    body = (
      <div className="grid gap-1">
        <Label htmlFor="merge-into">Into {intent.type}</Label>
        <NodePicker id="merge-into" type={intent.type} value={into} onChange={setInto} />
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>{TITLES[intent.kind]}</DialogTitle>
        {description && <DialogDescription>{description}</DialogDescription>}
      </DialogHeader>
      {body}
      <EvidenceFields value={evidence} onChange={setEvidence} />
      {error && (
        <div role="alert" className="rounded-md border border-conflict/40 bg-conflict/10 p-2.5 text-sm text-conflict">
          <div className="font-medium">{error.message}</div>
          {error instanceof ApiError && error.errors.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {error.errors.map((x) => <li key={x.index}>{x.message}</li>)}
            </ul>
          )}
        </div>
      )}
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit" variant={intent.kind === "delete" ? "danger" : "accent"} disabled={busy}>
          {busy ? "Saving…" : intent.kind === "delete" ? "Delete" : "Save"}
        </Button>
      </DialogFooter>
    </form>
  );
}
