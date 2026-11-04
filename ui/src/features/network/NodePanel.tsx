import { ArrowLeft, ArrowRight, GitMerge, Link2, MessagesSquare, MoreHorizontal, Pencil, Plus, SquarePlus, Trash2 } from "lucide-react";
import type { Network, NodeDetail } from "@/api/client";
import { EXPOSED, useNode } from "@/api/queries";
import { useSchema } from "@/api/graphSchema";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { SourceDetails } from "@/components/SourceLink";
import { ErrorNote, Loading } from "@/components/States";
import { MethodBadge } from "@/components/StatusBadge";
import { TypeIcon } from "@/components/TypeIcon";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { formatValue } from "@/lib/utils";
import type { EditIntent } from "./EditDialogs";
import { ExposureSection } from "./ExposureSection";
import { ChainSection, FacilityFigures, PartsSection, RisksSection } from "./FacilitySections";
import { FieldRow } from "./FieldRow";
import { BomSection, ComponentFlows, RiskOwner } from "./NodeSections";
import { Section } from "./PanelSection";

const DISCUSSED = ["Site", "Supplier", "Component", "Assembly"];
const NAME_FIELDS: Record<string, string> = { Component: "description", RiskIndicator: "indicator" };

function displayName(node: NodeDetail): string {
  const values = node.fields?.[NAME_FIELDS[node.type] ?? "name"];
  const active = values?.find((v) => v.active) ?? values?.[0];
  if (active) return formatValue(active.value);
  return node.type === "RiskIndicator" ? node.id.split("::").at(-1)! : node.id; // supplier::category::indicator
}

/** The details of one node in the side panel: its fields, then the sections that apply to its type (a section with
 *  no rows is left out), its sources, and every relation, collapsed, where edges are added and deleted. */
export function NodePanel({ type, id, network, onEdit, onOpen, onOpenLink, onDiscuss }: {
  type: string;
  id: string;
  network: Network | undefined;
  onEdit: (intent: EditIntent) => void;
  onOpen: (type: string, id: string) => void;
  onOpenLink: (supplier: string, site: string) => void;
  /** Offered for sites, suppliers, components and assemblies: a new Agent conversation about the node. */
  onDiscuss?: (type: string, id: string, name: string) => void;
}) {
  const { data: node, error, isLoading } = useNode(type, id);
  const { data: schema } = useSchema();
  if (isLoading) return <Loading />;
  if (error || !node) return <ErrorNote error={error ?? "not found"} className="m-4" />;
  if (node.hidden) {
    return (
      <div className="p-4 text-sm">
        <div className="mb-2 flex items-center gap-2 font-medium"><TypeIcon type={type} /> {type} {id}</div>
        {node.hidden === "merged" ? (
          <p>Merged into <button className="cursor-pointer text-interactive hover:underline" onClick={() => onOpen(type, node.merged_into!)}>{node.merged_into}</button>.</p>
        ) : (
          <p className="text-muted">Deleted{node.deletion?.reason ? `: ${String(node.deletion.reason)}` : ""}.</p>
        )}
      </div>
    );
  }
  const label = `${type} ${id}`;
  const fields = node.fields ?? {};
  const known = Object.keys(schema?.entities[type]?.fields ?? {});
  const order = [...known.filter((f) => f in fields), ...Object.keys(fields).filter((f) => !known.includes(f))];
  const facility = type === "Site" || type === "Supplier";
  const entity = facility ? network?.entities.find((e) => e.type === type && e.id === id) : undefined;
  const edges = node.edges ?? [];
  const sources = node.sources ?? [];
  return (
    <div className="flex flex-col" aria-label={label}>
      <div className="flex items-start gap-2 px-4 pt-4 pb-3">
        <TypeIcon type={type} className="mt-1 size-5" />
        <div className="min-w-0 flex-1">
          <h2 className="text-base leading-tight font-semibold break-words">{displayName(node)}</h2>
          <div className="text-xs text-muted">{type} · {id}{node.referenced_only ? " · referenced only" : ""}</div>
        </div>
        {onDiscuss && DISCUSSED.includes(type) && (
          <Button size="sm" variant="outline" onClick={() => onDiscuss(type, id, displayName(node))} title="Ask the agent about this node">
            <MessagesSquare /> Discuss
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon" variant="outline" className="size-8" aria-label="Edit node"><MoreHorizontal /></Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => onEdit({ kind: "set_field", type, key: [id], label })}><Pencil /> Set a field</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onEdit({ kind: "add_edge", from: { type, id } })}><Link2 /> Add an edge</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onEdit({ kind: "merge", type, id, label })}><GitMerge /> Merge into…</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onEdit({ kind: "create_node" })}><SquarePlus /> Create a node</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-conflict" onSelect={() => onEdit({ kind: "delete", type, key: [id], label })}><Trash2 /> Delete node</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {entity && network && <FacilityFigures entity={entity} exposureAvailable={network.exposure_available} />}

      <Section title="Fields" action={<Button size="xs" variant="ghost" onClick={() => onEdit({ kind: "set_field", type, key: [id], label })}><Plus /> Field</Button>}>
        {order.length === 0 ? (
          <p className="text-sm text-muted">No field values.</p>
        ) : (
          <table className="w-full table-fixed text-sm" aria-label="Fields">
            <colgroup>
              <col className="w-[30%]" />
              <col />
              <col className="w-14" />
              <col className="w-11" />
            </colgroup>
            <tbody>
              {order.map((f) => (
                <FieldRow
                  key={f}
                  name={f}
                  values={fields[f]}
                  onEdit={() => onEdit({ kind: "set_field", type, key: [id], label, field: f, current: fields[f].find((v) => v.active)?.value })}
                  onDelete={() => onEdit({ kind: "delete", type, key: [id], label, field: f })}
                />
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {facility && network && <ChainSection network={network} type={type} id={id} onOpen={onOpen} onOpenLink={onOpenLink} />}
      {facility && network && <PartsSection network={network} type={type} id={id} onOpen={onOpen} />}
      {type === "Supplier" && <RisksSection node={node} onOpen={onOpen} />}
      {type === "Component" && <ComponentFlows node={node} onOpen={onOpen} />}
      {type === "Assembly" && <BomSection assembly={id} root={node.root} onOpen={onOpen} />}
      {type === "RiskIndicator" && <RiskOwner node={node} onOpen={onOpen} />}

      {EXPOSED.includes(type) && (
        <Section title="Exposure">
          <ExposureSection type={type} id={id} onOpen={onOpen} />
        </Section>
      )}

      {sources.length > 0 && (
        <Section title="Sources">
          <ul className="flex flex-col gap-2">
            {sources.map((s, i) => (
              <li key={i} className="flex flex-col gap-1">
                <div className="flex items-center gap-1.5">
                  <MethodBadge method={s.method} />
                  <ConfidenceBar value={s.confidence} />
                </div>
                <SourceDetails source={s.source} label={s.label} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section
        title={`All relations (${edges.length})`}
        collapsed
        action={<Button size="xs" variant="ghost" onClick={() => onEdit({ kind: "add_edge", from: { type, id } })}><Plus /> Edge</Button>}
      >
        <ul className="flex flex-col">
          {edges.map((e) => {
            const edgeLabel = `${e.type} ${e.key.filter((x) => x != null).join(" → ")}`;
            const context = Object.entries(e.identity).filter(([, v]) => v != null);
            return (
              <li key={e.id} className="group flex items-center gap-2 border-b py-1.5 text-sm last:border-b-0">
                {e.direction === "out" ? <ArrowRight className="size-3.5 text-muted" aria-label="outgoing" /> : <ArrowLeft className="size-3.5 text-muted" aria-label="incoming" />}
                <span className="w-24 shrink-0 text-xs text-muted">{e.type}</span>
                <TypeIcon type={e.other.type} />
                <button className="min-w-0 cursor-pointer truncate text-left hover:underline" title={`${e.other.type} ${e.other.id}`} onClick={() => onOpen(e.other.type, e.other.id)}>
                  {e.other.name}
                </button>
                {context.length > 0 && <span className="truncate text-xs text-muted">{context.map(([k, v]) => `${k} ${String(v)}`).join(", ")}</span>}
                <Button size="icon" variant="ghost" className="ml-auto size-6 opacity-0 group-hover:opacity-100 focus:opacity-100" aria-label={`Delete edge ${edgeLabel}`}
                  onClick={() => onEdit({ kind: "delete", type: e.type, key: e.key, label: edgeLabel })}>
                  <Trash2 />
                </Button>
              </li>
            );
          })}
        </ul>
      </Section>
    </div>
  );
}
