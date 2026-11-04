import { X } from "lucide-react";
import type { ReactNode } from "react";
import type { Network } from "@/api/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { options, resolveMetric } from "./filters";
import { DEFAULT_TIERS, FACILITY_TYPES, type FacilityType, type NetworkView } from "./view";

const TOP = [5, 10, 20, 50];

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <fieldset className="grid gap-1.5">
      <legend className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">{label}</legend>
      {children}
    </fieldset>
  );
}

function Chip({ on, onClick, children, label }: { on: boolean; onClick: () => void; children: ReactNode; label?: string }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={label}
      onClick={onClick}
      className={cn("cursor-pointer rounded-full border px-2.5 py-0.5 text-xs font-medium", on ? "border-interactive bg-interactive-soft text-interactive" : "text-muted hover:text-fg")}
    >
      {children}
    </button>
  );
}

export function Segmented<T extends string | number>({ value, options: choices, onChange, label }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex gap-1 rounded-lg bg-surface-2 p-0.5">
      {choices.map((c) => (
        <button
          key={String(c.value)}
          type="button"
          role="radio"
          aria-checked={value === c.value}
          onClick={() => onChange(c.value)}
          className={cn("flex-1 cursor-pointer rounded-md px-2 py-1 text-xs text-muted", value === c.value && "bg-surface font-medium text-fg shadow-sm")}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

const toggle = <T,>(list: T[], item: T) => (list.includes(item) ? list.filter((x) => x !== item) : [...list, item]);

function FacilityFilters({ view, update, network, graphMode }: {
  view: NetworkView;
  update: (patch: Partial<NetworkView>) => void;
  network: Network;
  graphMode: boolean;
}) {
  const { countries, tiers } = options(network);
  const metric = resolveMetric(view, network);
  const setTypes = (t: FacilityType) => {
    const next = toggle(view.types, t);
    update({ types: next.length ? FACILITY_TYPES.filter((x) => next.includes(x)) : view.types });
  };
  return (
    <>
      <Group label="Facilities">
        <div className="flex flex-wrap gap-1.5">
          {FACILITY_TYPES.map((t) => (
            <Chip key={t} on={view.types.includes(t)} onClick={() => setTypes(t)}>{t === "Site" ? "Sites" : "Suppliers"}</Chip>
          ))}
        </div>
      </Group>
      <Group label="Supplier tier">
        <div className="flex flex-wrap gap-1.5">
          {tiers.map((t) => (
            <Chip key={t} label={`Tier ${t}`} on={view.tiers.includes(t)} onClick={() => {
              const next = toggle(view.tiers, t);
              update({ tiers: next.length ? next.sort() : DEFAULT_TIERS });
            }}>
              Tier {t}
            </Chip>
          ))}
          {view.tiers.length < tiers.length && (
            <button type="button" className="cursor-pointer text-xs text-interactive hover:underline" onClick={() => update({ tiers })}>All tiers</button>
          )}
        </div>
      </Group>
      <Group label="Country">
        <div className="flex max-h-40 flex-col overflow-auto rounded-md border bg-surface px-2 py-1" role="group" aria-label="Countries">
          {countries.map((c) => (
            <label key={c} className="flex cursor-pointer items-center gap-2 py-0.5 text-sm">
              <input type="checkbox" checked={view.country.includes(c)} onChange={() => update({ country: toggle(view.country, c) })} />
              {c}
            </label>
          ))}
        </div>
        {view.country.length > 0 && (
          <button type="button" className="cursor-pointer justify-self-start text-xs text-interactive hover:underline" onClick={() => update({ country: [] })}>All countries</button>
        )}
      </Group>
      <Group label="Only with">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={view.risk} onChange={() => update({ risk: !view.risk })} /> Risk flags
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={view.ss} onChange={() => update({ ss: !view.ss })} /> Single-source parts
        </label>
      </Group>
      <Group label="Size and rank by">
        <Segmented
          label="Metric"
          value={metric}
          options={[{ value: "impact", label: "Business Impact" }, { value: "exposure", label: "Exposure" }]}
          onChange={(m) => update({ metric: m })}
        />
        <Segmented
          label="Top"
          value={view.top ?? 0}
          options={[{ value: 0, label: "All" }, ...TOP.map((n) => ({ value: n, label: `Top ${n}` }))]}
          onChange={(n) => update({ top: n || null })}
        />
      </Group>
      {graphMode && (
        <Group label="Layout">
          <Segmented
            label="Layout"
            value={view.layout ?? "hierarchy"}
            options={[{ value: "hierarchy", label: "Columns by tier" }, { value: "rings", label: "Rings" }]}
            onChange={(l) => update({ layout: l === "hierarchy" ? null : l })}
          />
        </Group>
      )}
    </>
  );
}

/** Every network filter in one closable panel over the canvas. */
export function FilterDrawer({ view, update, network, graphMode, onReset, onClose }: {
  view: NetworkView;
  update: (patch: Partial<NetworkView>) => void;
  network: Network | undefined;
  graphMode: boolean;
  onReset: () => void;
  onClose: () => void;
}) {
  return (
    <aside
      aria-label="Filters"
      className="absolute top-3 right-3 bottom-3 z-30 flex w-72 flex-col overflow-hidden rounded-xl border bg-surface shadow-lg"
    >
      <div className="flex items-center gap-2 border-b px-4 py-2.5">
        <h2 className="text-sm font-semibold">Filters</h2>
        <button type="button" className="ml-auto cursor-pointer text-xs text-interactive hover:underline" onClick={onReset}>Reset</button>
        <Button size="icon" variant="ghost" className="size-7" aria-label="Close filters" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="flex flex-col gap-4 overflow-auto p-4">
        {network && <FacilityFilters view={view} update={update} network={network} graphMode={graphMode} />}
      </div>
    </aside>
  );
}
