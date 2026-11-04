import { AlertTriangle, ChevronDown, ChevronRight, Lock, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { FieldValue } from "@/api/client";
import { ConfidenceBar, ConfidenceDot } from "@/components/ConfidenceBar";
import { SourceDetails } from "@/components/SourceLink";
import { MethodBadge, StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { cn, formatValue, humanize } from "@/lib/utils";

/** What a candidate is to a reader: the active value, waiting for review, or replaced. */
export function candidateStatus(v: FieldValue): string {
  if (v.active) return v.status === "flagged" ? "flagged" : "active";
  if (v.review === "proposed") return "proposed";
  if (v.review === "rejected") return "rejected";
  return "superseded";
}

/** One field as a table row: name, active value, confidence and a state cell (locked, flagged, other candidates).
 *  The arrow opens a full-width row with the method, status and every candidate with its source. */
export function FieldRow({ name, values, onEdit, onDelete }: {
  name: string;
  values: FieldValue[];
  onEdit?: () => void;
  onDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const active = values.find((v) => v.active);
  const shown = active ?? values[0];
  const others = values.length - (active ? 1 : 0);
  const label = humanize(name);
  const value = formatValue(shown?.value);
  return (
    <>
      <tr className={cn("group border-b align-middle", open && "border-b-0 bg-surface-2/60")} data-field={name}>
        <td className="py-1.5 pr-2">
          <button
            className="flex w-full cursor-pointer items-center gap-1 text-left text-xs text-muted hover:text-fg"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} sources of ${label}`}
          >
            {open ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
            <span className="truncate" title={label}>{label}</span>
          </button>
        </td>
        <td className="relative max-w-0 py-1.5 pr-2">
          <div className={cn("truncate", !active && "text-muted italic")} title={value} data-testid={`field-${name}`}>
            {value}
            {!active && shown && <span className="ml-1 not-italic">({candidateStatus(shown)})</span>}
          </div>
          {/* actions float over the value on hover and focus, so the value keeps the width */}
          <span className="absolute top-1/2 right-1 inline-flex -translate-y-1/2 gap-0.5 rounded-md bg-surface opacity-0 shadow-sm transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            {onEdit && (
              <Button size="icon" variant="ghost" className="size-6" onClick={onEdit} aria-label={`Change ${label}`}>
                <Pencil />
              </Button>
            )}
            {onDelete && (
              <Button size="icon" variant="ghost" className="size-6" onClick={onDelete} aria-label={`Delete value of ${label}`}>
                <Trash2 />
              </Button>
            )}
          </span>
        </td>
        <td className="py-1.5 pr-2"><ConfidenceDot value={shown?.confidence} /></td>
        <td className="py-1.5">
          <span className="flex items-center gap-1 text-xs text-muted" data-testid={`state-${name}`}>
            {shown?.locked && <Lock className="size-3.5 text-brand" aria-label="locked" />}
            {active?.status === "flagged" && <AlertTriangle className="size-3.5 text-conflict" aria-label="flagged" />}
            {others > 0 && <span className="tabular" aria-label={`${others} other ${others === 1 ? "candidate" : "candidates"}`}>+{others}</span>}
          </span>
        </td>
      </tr>
      {open && (
        <tr className="border-b bg-surface-2/60">
          <td colSpan={4} className="px-1 pb-2">
            {shown && (
              <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                <MethodBadge method={shown.method} />
                <StatusBadge status={candidateStatus(shown)} />
              </div>
            )}
            <ol className="flex flex-col gap-2" aria-label={`Candidates of ${label}`}>
              {values.map((v, i) => (
                <li key={i} className={cn("rounded-md border p-2", v.active ? "border-brand/40 bg-brand-soft/40" : "bg-surface-2/50")}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={cn("text-sm font-medium", !v.active && "text-muted")}>{formatValue(v.value)}</span>
                    <StatusBadge status={candidateStatus(v)} />
                    {v.locked && (
                      <span className="inline-flex items-center gap-1 text-xs text-brand">
                        <Lock className="size-3" aria-hidden /> locked
                      </span>
                    )}
                    <MethodBadge method={v.method} />
                    <ConfidenceBar value={v.confidence} className="ml-auto" />
                  </div>
                  <div className="mt-1.5">
                    <SourceDetails source={v.source} label={v.label} />
                  </div>
                  {v.proposal_id != null && (
                    <Link to={`/data/review/${v.proposal_id}`} className="mt-1 inline-block text-xs text-interactive hover:underline">
                      proposal #{v.proposal_id}
                    </Link>
                  )}
                </li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </>
  );
}
