import { Anchor, Ban, BadgePercent, CircuitBoard, Factory, Flame, type LucideIcon } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import type { DisruptionType } from "@/api/ops";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const ICONS: Record<DisruptionType, LucideIcon> = {
  supplier_outage: Flame,
  export_restriction: Ban,
  route_closure: Anchor,
  plant_outage: Factory,
  tariff: BadgePercent,
  shortage: CircuitBoard,
};

export function TypeIcon({ type, className }: { type: DisruptionType; className?: string }) {
  const Icon = ICONS[type] ?? Flame;
  return <Icon className={className} aria-hidden />;
}

export function formatSkill(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : value.toFixed(2);
}

export function Tile({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "bad" | "good" }) {
  return (
    <div className="flex flex-col gap-1 card rounded-xl border bg-surface px-4 py-3">
      <span className="text-xs text-muted">{label}</span>
      <span className={cn("text-xl font-semibold tracking-tight tabular", tone === "bad" && "text-risk", tone === "good" && "text-ok")}>{value}</span>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}

export interface Option {
  value: string;
  label: string;
}

/** Pick several values from a list by typing; chosen values show as removable chips. */
export function ChipSelect({
  options,
  value,
  onChange,
  placeholder,
  id,
}: {
  options: Option[];
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  id?: string;
}) {
  const [text, setText] = useState("");
  const listId = `${id ?? "chips"}-options`;
  const byLabel = new Map(options.map((o) => [o.label.toLowerCase(), o.value]));
  const byValue = new Map(options.map((o) => [o.value, o.label]));
  const add = (raw: string) => {
    const v = byLabel.get(raw.trim().toLowerCase()) ?? (byValue.has(raw.trim()) ? raw.trim() : undefined);
    if (v && !value.includes(v)) onChange([...value, v]);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      add(text);
    } else if (e.key === "Backspace" && !text && value.length) {
      onChange(value.slice(0, -1));
    }
  };
  return (
    <div className="flex min-h-9 flex-wrap items-center gap-1 rounded-md border bg-surface px-1.5 py-0.5">
      {value.map((v) => (
        <span key={v} className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-xs">
          {byValue.get(v) ?? v}
          <button type="button" className="text-muted hover:text-fg" aria-label={`Remove ${byValue.get(v) ?? v}`} onClick={() => onChange(value.filter((x) => x !== v))}>
            ×
          </button>
        </span>
      ))}
      <Input
        id={id}
        list={listId}
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          // choosing from the datalist fills the whole label at once
          if (byLabel.has(next.trim().toLowerCase())) add(next);
          else setText(next);
        }}
        onKeyDown={onKey}
        placeholder={value.length ? "" : placeholder}
        className="h-7 min-w-40 flex-1 border-0 px-1 focus-visible:outline-0"
      />
      <datalist id={listId}>
        {options
          .filter((o) => !value.includes(o.value))
          .map((o) => (
            <option key={o.value} value={o.label} />
          ))}
      </datalist>
    </div>
  );
}
