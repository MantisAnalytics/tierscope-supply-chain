import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { assess, type Assessment, type Disruption, type DisruptionType, type OpsMeta } from "@/api/ops";
import { ErrorNote } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { formatEur } from "@/lib/utils";
import { ChipSelect, type Option } from "../shared";

export const EMPTY: Disruption = {
  name: "",
  type: "supplier_outage",
  start_week: 0,
  duration_weeks: 8,
  description: "",
  params: { suppliers: [], lost_share: 1 },
  stock_cover_weeks: 3,
  horizon_weeks: 26,
  source: "form",
};

const DEFAULT_PARAMS: Record<DisruptionType, Record<string, unknown>> = {
  supplier_outage: { suppliers: [], lost_share: 1 },
  export_restriction: { countries: [], commodities: [], lost_share: 1 },
  route_closure: { origin_countries: [], destination_countries: [], extra_weeks: 3, surcharge: 0.2 },
  plant_outage: { sites: [] },
  tariff: { origin_countries: [], destination_countries: [], rate: 0.25 },
  shortage: { commodities: [], curve: [] },
};

const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);

function Field({ label, htmlFor, children, hint }: { label: string; htmlFor?: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="grid content-start gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}

/** Edit a disruption; the impact preview runs the do-nothing simulation without saving. */
export function DisruptionForm({
  meta,
  initial,
  onSubmit,
  submitLabel,
  busy,
}: {
  meta: OpsMeta;
  initial: Disruption;
  onSubmit: (d: Disruption) => void;
  submitLabel: string;
  busy?: boolean;
}) {
  const [d, setD] = useState<Disruption>(initial);
  const [preview, setPreview] = useState<Assessment | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [episode, setEpisode] = useState<string>("");
  useEffect(() => setD(initial), [initial]);

  const set = (patch: Partial<Disruption>) => {
    setD((x) => ({ ...x, ...patch }));
    setPreview(null);
  };
  const setParam = (key: string, value: unknown) => set({ params: { ...d.params, [key]: value } });

  const countryOptions = (which: "suppliers" | "sites" | "both"): Option[] => {
    const names = which === "both" ? [...new Set([...meta.countries.suppliers, ...meta.countries.sites])].sort() : meta.countries[which];
    return [...meta.continents.map((c) => ({ value: c, label: `${c} (all countries)` })), ...names.map((c) => ({ value: c, label: c }))];
  };
  const supplierOptions = meta.suppliers.map((s) => ({ value: s.id, label: `${s.name} · ${s.country ?? "?"} · ${s.id}` }));
  const siteOptions = meta.sites.map((s) => ({ value: s.id, label: `${s.id} · ${s.name} · ${s.country ?? "?"}` }));
  const commodityOptions = meta.commodities.map((c) => ({ value: c, label: c }));

  const loadEpisode = async (ep: string) => {
    setEpisode(ep);
    if (!ep) return;
    const r = await api<{ curve: number[] }>(`/ops/shockbench/${ep}`);
    set({ params: { ...d.params, curve: r.curve }, duration_weeks: r.curve.length, source: "shockbench" });
  };

  const runPreview = async () => {
    setPreviewError(null);
    try {
      setPreview(await assess(d));
    } catch (e) {
      setPreviewError(e);
    }
  };

  const p = d.params;
  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(d);
      }}
    >
      <div className="grid grid-cols-[2fr_1fr] gap-3">
        <Field label="Name" htmlFor="d-name">
          <Input id="d-name" value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Fire at a coating supplier" required />
        </Field>
        <Field label="Type" htmlFor="d-type">
          <Select
            id="d-type"
            value={d.type}
            onChange={(e) => {
              const type = e.target.value as DisruptionType;
              set({ type, params: { ...DEFAULT_PARAMS[type] } });
            }}
          >
            {meta.types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <Field label="Starts in week" htmlFor="d-start" hint="0 = already happening">
          <Input id="d-start" type="number" min={0} max={d.horizon_weeks - 1} value={d.start_week} onChange={(e) => set({ start_week: Number(e.target.value) })} />
        </Field>
        <Field label="Lasts (weeks)" htmlFor="d-dur">
          <Input id="d-dur" type="number" min={1} max={52} value={d.duration_weeks} onChange={(e) => set({ duration_weeks: Number(e.target.value) })} />
        </Field>
        <Field label="Stock cover (weeks)" htmlFor="d-cover" hint="assumed for every part">
          <Input id="d-cover" type="number" min={0} max={12} step={0.5} value={d.stock_cover_weeks} onChange={(e) => set({ stock_cover_weeks: Number(e.target.value) })} />
        </Field>
        <Field label="Horizon (weeks)" htmlFor="d-hor">
          <Input id="d-hor" type="number" min={4} max={52} value={d.horizon_weeks} onChange={(e) => set({ horizon_weeks: Number(e.target.value) })} />
        </Field>
      </div>

      {d.type === "supplier_outage" && (
        <div className="grid grid-cols-[3fr_1fr] gap-3">
          <Field label="Suppliers" htmlFor="d-sup">
            <ChipSelect id="d-sup" options={supplierOptions} value={list(p.suppliers)} onChange={(v) => setParam("suppliers", v)} placeholder="Type a supplier name or id" />
          </Field>
          <Field label="Capacity lost" htmlFor="d-lost" hint="1 = everything">
            <Input id="d-lost" type="number" min={0.05} max={1} step={0.05} value={num(p.lost_share, 1)} onChange={(e) => setParam("lost_share", Number(e.target.value))} />
          </Field>
        </div>
      )}
      {d.type === "export_restriction" && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Supplier countries" htmlFor="d-ctry">
            <ChipSelect id="d-ctry" options={countryOptions("suppliers")} value={list(p.countries)} onChange={(v) => setParam("countries", v)} placeholder="e.g. China" />
          </Field>
          <Field label="Commodities (empty = all)" htmlFor="d-comm">
            <ChipSelect id="d-comm" options={commodityOptions} value={list(p.commodities)} onChange={(v) => setParam("commodities", v)} placeholder="e.g. CHEMICALS" />
          </Field>
        </div>
      )}
      {(d.type === "route_closure" || d.type === "tariff") && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Goods leaving from" htmlFor="d-orig">
            <ChipSelect id="d-orig" options={countryOptions("suppliers")} value={list(p.origin_countries)} onChange={(v) => setParam("origin_countries", v)} placeholder="Countries or continents" />
          </Field>
          <Field label="Going to (empty = everywhere)" htmlFor="d-dest">
            <ChipSelect id="d-dest" options={countryOptions("sites")} value={list(p.destination_countries)} onChange={(v) => setParam("destination_countries", v)} placeholder="Countries or continents" />
          </Field>
          {d.type === "route_closure" ? (
            <>
              <Field label="Extra transit (weeks)" htmlFor="d-extra">
                <Input id="d-extra" type="number" min={1} max={12} value={num(p.extra_weeks, 3)} onChange={(e) => setParam("extra_weeks", Number(e.target.value))} />
              </Field>
              <Field label="Freight surcharge (share of value)" htmlFor="d-sur">
                <Input id="d-sur" type="number" min={0} max={1} step={0.05} value={num(p.surcharge, 0.2)} onChange={(e) => setParam("surcharge", Number(e.target.value))} />
              </Field>
            </>
          ) : (
            <Field label="Tariff rate (0.25 = 25%)" htmlFor="d-rate">
              <Input id="d-rate" type="number" min={0.01} max={5} step={0.05} value={num(p.rate, 0.25)} onChange={(e) => setParam("rate", Number(e.target.value))} />
            </Field>
          )}
        </div>
      )}
      {d.type === "plant_outage" && (
        <Field label="Our plants" htmlFor="d-sites">
          <ChipSelect id="d-sites" options={siteOptions} value={list(p.sites)} onChange={(v) => setParam("sites", v)} placeholder="Type a plant id or name" />
        </Field>
      )}
      {d.type === "shortage" && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Commodities" htmlFor="d-scomm">
            <ChipSelect id="d-scomm" options={commodityOptions} value={list(p.commodities)} onChange={(v) => setParam("commodities", v)} placeholder="e.g. ELECTROMECHANICS & A" />
          </Field>
          <Field label="Weekly supply curve from a ShockBench episode" htmlFor="d-ep" hint={list(p.curve).length ? `${list(p.curve).length} weeks, low point ${Math.min(...(p.curve as number[])).toFixed(2)}` : "the chip market's served share, week by week"}>
            <Select id="d-ep" value={episode} onChange={(e) => loadEpisode(e.target.value)}>
              <option value="">Choose an episode…</option>
              {meta.shockbench_episodes.map((e) => (
                <option key={e.episode} value={e.episode}>
                  Episode {e.episode}: {e.weeks} weeks, low point {e.low.toFixed(2)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      )}

      <Field label="Description" htmlFor="d-desc">
        <Textarea id="d-desc" value={d.description} onChange={(e) => set({ description: e.target.value })} placeholder="What happened and what it blocks" />
      </Field>

      {previewError ? <ErrorNote error={previewError} /> : null}
      {preview && (
        <div className="rounded-lg border bg-surface-2 px-3 py-2 text-sm">
          If nothing is done: <span className="font-semibold text-risk tabular">{formatEur(preview.do_nothing.total_cost)}</span> total cost,{" "}
          <span className="tabular">{formatEur(preview.do_nothing.lost_revenue)}</span> lost revenue,{" "}
          {preview.affected_components.length} components and {preview.assemblies.length} assemblies affected
          {preview.do_nothing.first_stop_week !== null ? `, production stops in week ${preview.do_nothing.first_stop_week}` : ", production keeps running"}.
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button type="submit" variant="accent" disabled={busy || !d.name.trim()}>
          {submitLabel}
        </Button>
        <Button type="button" variant="outline" onClick={runPreview}>
          Preview impact
        </Button>
      </div>
    </form>
  );
}
