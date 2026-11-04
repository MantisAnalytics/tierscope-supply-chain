import { Factory, Map as MapIcon, Network as NetworkIcon, Plus, SlidersHorizontal } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useNetwork, useNode, useOverview } from "@/api/queries";
import { ErrorNote, Loading } from "@/components/States";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { highlightOf } from "@/features/network/chain";
import { EditDialog, type EditIntent } from "@/features/network/EditDialogs";
import { FilterDrawer } from "@/features/network/FilterDrawer";
import { filterNetwork } from "@/features/network/filters";
import { crumbsOf, panelMoves, SidePanel } from "@/features/network/SidePanel";
import { SliceSummary } from "@/features/network/SliceSummary";
import { activeFilters, DEFAULT_TIERS, FACILITY_TYPES, nodeKey, useNetworkView, type NetworkView } from "@/features/network/view";
import { Kpis } from "./Kpis";
import { Operations } from "./Operations";
import { TopRisks } from "./TopRisks";

// The map and the graph code load only when their mode is shown.
const MapView = lazy(() => import("@/features/network/MapView"));
const FacilityGraph = lazy(() => import("@/features/network/FacilityGraph"));

const RESET: Partial<NetworkView> = { types: FACILITY_TYPES, tiers: DEFAULT_TIERS, country: [], risk: false, ss: false, metric: null, top: null };

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

/** The home page: key figures on top, the facility network in the centre, and a side panel with the company
 *  summary or the selected facility or link and what was opened from it. Every view setting lives in the address. */
export function OverviewPage() {
  const { data: overview, error: overviewError } = useOverview();
  const { data: network, error: networkError } = useNetwork();
  const [view, update] = useNetworkView();
  const [drawer, setDrawer] = useState(false);
  const [intent, setIntent] = useState<EditIntent | null>(null);

  const crumbs = crumbsOf(view);
  const deepest = crumbs.at(-1) ?? null;
  const isAssembly = deepest?.type === "Assembly";
  const { data: assembly } = useNode(isAssembly ? "Assembly" : undefined, isAssembly ? deepest.id : undefined);
  const deepKey = deepest && nodeKey(deepest);
  // What the network highlights (and keeps visible whatever the filters): the selection's chain, a link's ends,
  // or where the part or assembly in the panel comes from and goes to.
  const highlight = useMemo(
    () => (network ? highlightOf(network, deepest, assembly?.components ?? undefined) : null),
    [network, deepKey, assembly], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const slice = useMemo(() => (network ? filterNetwork(network, view, highlight) : null), [network, view, highlight]);
  const mode = view.view ?? (network && !network.entities.some((e) => e.latitude != null) ? "graph" : "map");

  const moves = panelMoves(view, update);
  const select = moves.open;
  const clear = moves.summary;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || isTyping(e.target) || document.querySelector('[role="dialog"]')) return;
      if (crumbs.length) clear();
      else if (drawer) setDrawer(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const filters = activeFilters(view);
  const canvasProps = slice && network && {
    slice,
    exposureAvailable: network.exposure_available,
    selected: view.sel ? nodeKey(view.sel) : null,
    link: view.link ? `${view.link.supplier}>${view.link.site}` : null,
    highlight,
    onSelect: select,
    onSelectLink: (supplier: string, site: string) => update({ link: { supplier, site }, sel: null, trail: [] }),
    onClear: clear,
  };

  let canvas;
  if (networkError) {
    canvas = <ErrorNote error={networkError} className="m-4" />;
  } else if (!canvasProps) {
    canvas = <Loading label="Loading network…" />;
  } else if (canvasProps.slice.entities.length === 0) {
    canvas = (
      <div className="chrome flex h-full flex-col items-center justify-center gap-3 text-center text-muted" data-testid="network-empty">
        <Factory className="size-10" />
        <p className="text-sm">No facility matches the filters.</p>
        <Button size="sm" variant="outline" onClick={() => update(RESET)}>Reset filters</Button>
      </div>
    );
  } else if (mode === "map") {
    canvas = (
      <Suspense fallback={<Loading label="Loading map…" />}>
        <MapView {...canvasProps} facilities={network!.entities} onShowGraph={() => update({ view: "graph" })} />
      </Suspense>
    );
  } else {
    canvas = (
      <Suspense fallback={<Loading label="Loading graph…" />}>
        <FacilityGraph {...canvasProps} layout={view.layout ?? "hierarchy"} />
      </Suspense>
    );
  }

  let panel;
  if (crumbs.length) {
    panel = <SidePanel view={view} update={update} network={network} onEdit={setIntent} />;
  } else {
    panel = (
      <div className="flex flex-col gap-4 p-4">
        {slice && filters > 0 && <SliceSummary slice={slice} onSelect={select} />}
        <Operations />
        {overview && <TopRisks overview={overview} onSelect={select} />}
        {overview && (
          <Card>
            <CardHeader>
              <CardTitle>Review</CardTitle>
              <Link to="/data/review" className="text-xs text-interactive hover:underline">Open queue</Link>
            </CardHeader>
            <CardContent>
              {overview.pending_total === 0 ? (
                <p className="text-sm text-muted">Nothing waits for review.</p>
              ) : (
                <p className="text-sm">
                  <span className="font-semibold tabular" data-testid="pending-total">{overview.pending_total}</span>{" "}
                  {overview.pending_total === 1 ? "proposal waits" : "proposals wait"} for review
                  <span className="text-muted"> ({Object.entries(overview.pending_proposals).map(([kind, n]) => `${n} ${kind}`).join(", ")})</span>
                </p>
              )}
            </CardContent>
          </Card>
        )}
        <Button variant="outline" size="sm" className="self-start" onClick={() => setIntent({ kind: "create_node" })}>
          <Plus /> Create node
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {overview ? <Kpis overview={overview} /> : overviewError ? <ErrorNote error={overviewError} className="m-4" /> : <div className="h-[62px] shrink-0 border-b" />}
      <div className="grid min-h-0 flex-1 grid-cols-[1fr_420px]">
        <section aria-label="Network" className="flex min-w-0 flex-col">
          <div className="flex items-center gap-3 border-b bg-surface px-3 py-2">
            <div role="radiogroup" aria-label="Mode" className="flex gap-1 rounded-lg bg-surface-2 p-0.5">
              {([["map", "Map", MapIcon], ["graph", "Graph", NetworkIcon]] as const).map(([m, label, Icon]) => (
                <button
                  key={m}
                  role="radio"
                  aria-checked={mode === m}
                  onClick={() => update({ view: m })}
                  className={`flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-xs ${mode === m ? "bg-surface font-medium text-fg shadow-sm" : "text-muted"}`}
                >
                  <Icon className="size-3.5" /> {label}
                </button>
              ))}
            </div>
            {slice && (
              <span className="text-xs text-muted tabular" data-testid="slice-count">
                {slice.entities.length} facilities · {slice.supply.length} supply links
              </span>
            )}
            <Button
              size="sm"
              variant={drawer ? "default" : "outline"}
              className="ml-auto"
              aria-expanded={drawer}
              aria-label={`Filters, ${filters} active`}
              onClick={() => setDrawer(!drawer)}
            >
              <SlidersHorizontal /> Filters
              {filters > 0 && <span className="rounded-full bg-interactive px-1.5 text-[11px] leading-4 font-semibold text-interactive-fg tabular" data-testid="filter-count">{filters}</span>}
            </Button>
          </div>
          <div className="relative min-h-0 flex-1 bg-surface">
            {canvas}
            {drawer && (
              <FilterDrawer
                view={view}
                update={update}
                network={network}
                graphMode={mode === "graph"}
                onReset={() => update(RESET)}
                onClose={() => setDrawer(false)}
              />
            )}
          </div>
        </section>
        <aside
          aria-label={deepest?.type === "Link" ? "Link details" : deepest ? "Node details" : "Summary"}
          className="min-h-0 overflow-auto border-l bg-bg"
        >
          {crumbs.length ? <div className="min-h-full bg-surface">{panel}</div> : panel}
        </aside>
      </div>
      <EditDialog
        intent={intent}
        onClose={() => setIntent(null)}
        onDone={(edit) => {
          if (edit.op === "create_node") select(String(edit.type), String(edit.id));
          if (edit.op === "merge") moves.replace(String(edit.type), String(edit.into));
        }}
      />
    </div>
  );
}
