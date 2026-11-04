import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { NodeDetail } from "@/api/client";
import { useNode } from "@/api/queries";
import { ErrorNote, Loading } from "@/components/States";
import { TypeIcon } from "@/components/TypeIcon";
import { formatValue } from "@/lib/utils";
import { OpenCell, Section } from "./PanelSection";

type Open = (type: string, id: string) => void;
type Edge = NonNullable<NodeDetail["edges"]>[number];

const active = (e: Edge, field: string) => e.fields[field]?.find((v) => v.active)?.value;

function FacilityRows({ title, rows, onOpen }: { title: string; rows: { edge: Edge; note?: string }[]; onOpen: Open }) {
  if (rows.length === 0) return null;
  return (
    <div role="group" aria-label={title}>
      <div className="py-1 text-xs font-medium">{title} ({rows.length})</div>
      <ul className="flex flex-col">
        {rows.map(({ edge, note }) => (
          <li key={edge.id} className="flex items-center gap-2 border-b py-1 text-sm last:border-b-0">
            <TypeIcon type={edge.other.type} />
            <button className="min-w-0 cursor-pointer truncate text-left hover:underline" title={edge.other.name} onClick={() => onOpen(edge.other.type, edge.other.id)}>
              {edge.other.name}
            </button>
            {note && <span className="ml-auto shrink-0 truncate text-xs text-muted">{note}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Where a part comes from and where it goes: its suppliers (with the site each ships to) and its sites. */
export function ComponentFlows({ node, onOpen }: { node: NodeDetail; onOpen: Open }) {
  const edges = node.edges ?? [];
  const from = edges.filter((e) => e.type === "SUPPLIES" && e.direction === "in");
  const to = edges.filter((e) => e.type === "DELIVERED_TO" && e.direction === "out");
  if (from.length + to.length === 0) return null;
  return (
    <Section title="Supply">
      <div className="flex flex-col gap-2">
        <FacilityRows title="Supplied by" rows={from.map((edge) => ({ edge, note: edge.identity.site ? `to ${String(edge.identity.site)}` : undefined }))} onOpen={onOpen} />
        <FacilityRows title="Delivered to" rows={to.map((edge) => ({ edge }))} onOpen={onOpen} />
      </div>
    </Section>
  );
}

/** The supplier a risk indicator belongs to. */
export function RiskOwner({ node, onOpen }: { node: NodeDetail; onOpen: Open }) {
  const owners = (node.edges ?? []).filter((e) => e.type === "HAS_RISK" && e.direction === "in");
  if (owners.length === 0) return null;
  return (
    <Section title="Supplier">
      <FacilityRows title="Belongs to" rows={owners.map((edge) => ({ edge }))} onOpen={onOpen} />
    </Section>
  );
}

/** The parts one level below a component in an assembly's BOM: its CONTAINS edges for that assembly. */
function BomChildren({ id, assembly, level, onOpen }: { id: string; assembly: string; level: number; onOpen: Open }) {
  const { data, error, isLoading } = useNode("Component", id);
  if (isLoading) return <Loading label="Loading parts…" />;
  if (error || !data) return <ErrorNote error={error ?? "not found"} />;
  const children = (data.edges ?? []).filter((e) => e.type === "CONTAINS" && e.direction === "out" && e.identity.assembly === assembly);
  if (children.length === 0) return <p className="py-1 pl-5 text-xs text-muted">No parts below.</p>;
  return (
    <ul role="group">
      {children.map((e) => (
        <BomItem key={e.id} id={e.other.id} description={e.other.values?.description} quantity={active(e, "quantity")} assembly={assembly} level={level} onOpen={onOpen} />
      ))}
    </ul>
  );
}

function BomItem({ id, description, quantity, assembly, level, open: initial = false, onOpen }: {
  id: string;
  description?: unknown;
  quantity?: unknown;
  assembly: string;
  level: number;
  open?: boolean;
  onOpen: Open;
}) {
  const [open, setOpen] = useState(initial);
  return (
    <li role="treeitem" aria-expanded={open} aria-level={level} aria-label={id} className="flex flex-col">
      <div className="flex items-center gap-1 border-b py-1 text-sm" style={{ paddingLeft: (level - 1) * 14 }}>
        <button className="shrink-0 cursor-pointer text-muted" aria-label={`${open ? "Collapse" : "Expand"} ${id}`} onClick={() => setOpen(!open)}>
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <OpenCell label={id} sub={description != null ? String(description) : null} onClick={() => onOpen("Component", id)} />
        </div>
        {quantity != null && <span className="shrink-0 text-xs text-muted tabular">×{formatValue(quantity)}</span>}
      </div>
      {open && <BomChildren id={id} assembly={assembly} level={level + 1} onOpen={onOpen} />}
    </li>
  );
}

/** An assembly's BOM as a tree from its root component; each level loads when opened, the first is open. */
export function BomSection({ assembly, root, onOpen }: { assembly: string; root: string | null | undefined; onOpen: Open }) {
  if (!root) return null;
  return (
    <Section title="BOM">
      <ul role="tree" aria-label={`BOM of ${assembly}`}>
        <BomItem id={root} assembly={assembly} level={1} open onOpen={onOpen} />
      </ul>
    </Section>
  );
}
