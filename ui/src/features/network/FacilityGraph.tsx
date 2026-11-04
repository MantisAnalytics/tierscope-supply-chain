import type { Core, ElementDefinition, LayoutOptions, StylesheetJson } from "cytoscape";
import { Maximize } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "@/app/theme";
import { Button } from "@/components/ui/button";
import { entityKey, linkKey, sizer, tierOf, type Slice } from "./filters";
import { canDraw, cytoscape } from "./cytoscape";
import { LinkTooltip, type LinkHover } from "./LinkTooltip";
import { colourer, RISK_HALO, TYPE_OUTLINE, type FacilityCanvasProps } from "./marks";
import type { LayoutChoice } from "./view";

const COLUMN = 260; // between tiers
const ROWS = 24; // a tier taller than this wraps into side-by-side sub-columns
const ROW = 34;
const SUB = 150;

/** Material flows left to right: the highest supplier tier in the first column, tier 1 next, sites last. */
function columns(slice: Slice) {
  const maxTier = Math.max(1, ...slice.entities.map((e) => tierOf(e) ?? 1));
  return (type: string, tier: number | null) => (type === "Site" ? maxTier : maxTier - (tier ?? 1));
}

export function facilityElements(slice: Slice, available: boolean): ElementDefinition[] {
  const size = sizer(slice.entities, slice.metric);
  const fill = colourer(slice.entities, available);
  const column = columns(slice);
  const nodes: ElementDefinition[] = slice.entities.map((e) => ({
    group: "nodes",
    data: {
      id: entityKey(e),
      label: e.name.length > 26 ? `${e.name.slice(0, 25)}…` : e.name,
      type: e.type,
      nodeId: e.id,
      column: column(e.type, tierOf(e)),
      size: 12 + size(e) * 30,
      fill: fill(e),
    },
    classes: e.risk_flags > 0 ? "risk" : "",
  }));
  const edges: ElementDefinition[] = [
    ...slice.supply.map((l) => ({
      group: "edges" as const,
      data: { id: `L:${linkKey(l)}`, source: `Supplier:${l.supplier}`, target: `Site:${l.site}`, kind: "supply", supplier: l.supplier, site: l.site },
    })),
    ...slice.tier.map((l) => ({
      group: "edges" as const,
      data: { id: `T:${l.supplier}^${l.parent}`, source: `Supplier:${l.supplier}`, target: `Supplier:${l.parent}`, kind: "tier" },
    })),
  ];
  return [...nodes, ...edges];
}

/** A theme colour from the document, for Cytoscape, which cannot read CSS variables. */
function cssVar(name: string, fallback: string): string {
  if (typeof getComputedStyle !== "function") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function stylesheet(): StylesheetJson {
  const fg = cssVar("--fg", "#1f1d24");
  const muted = cssVar("--muted", "#5f5c66");
  const surface = cssVar("--surface", "#ffffff");
  const interactive = cssVar("--interactive", "#0a63d1");
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-family": "Inter Variable, system-ui, sans-serif",
        "font-size": 9,
        color: fg,
        "text-valign": "bottom",
        "text-margin-y": 3,
        "text-background-color": surface,
        "text-background-opacity": 0.7,
        "text-background-padding": "1px",
        "min-zoomed-font-size": 7,
        width: "data(size)",
        height: "data(size)",
        "background-color": "data(fill)",
        "border-width": 2,
        "border-color": TYPE_OUTLINE.Supplier,
        shape: "ellipse",
      },
    },
    { selector: 'node[type = "Site"]', style: { shape: "round-rectangle", "border-color": TYPE_OUTLINE.Site } },
    { selector: "node.risk", style: { "underlay-color": RISK_HALO, "underlay-opacity": 0.3, "underlay-padding": 5, "underlay-shape": "ellipse" } },
    { selector: "node:selected", style: { "border-color": interactive, "border-width": 3, "overlay-opacity": 0 } },
    {
      selector: "edge",
      style: {
        width: 1,
        "line-color": interactive,
        "target-arrow-color": interactive,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.6,
        "curve-style": "bezier",
        opacity: 0.35,
        // a wider invisible hit area around thin links
        "overlay-padding": 6,
        "overlay-opacity": 0,
      },
    },
    { selector: 'edge[kind = "tier"]', style: { "line-color": muted, "target-arrow-color": muted, "line-style": "dashed", opacity: 0.4 } },
    { selector: "edge.hover, edge:selected", style: { opacity: 1, width: 2.4 } },
    { selector: "node.faded", style: { opacity: 0.2 } },
    { selector: "edge.faded", style: { opacity: 0.05 } },
  ];
}

function layoutOptions(kind: LayoutChoice): LayoutOptions {
  if (kind === "rings") {
    return {
      name: "concentric",
      animate: false,
      fit: true,
      padding: 24,
      minNodeSpacing: 8,
      avoidOverlap: true,
      // sites in the centre, each supplier tier one ring further out
      concentric: (n: { data(k: string): number }) => n.data("column"),
      levelWidth: () => 1,
    } as LayoutOptions;
  }
  return {
    name: "dagre",
    rankDir: "LR",
    nodeSep: 6,
    rankSep: COLUMN,
    animate: false,
    fit: false,
  } as LayoutOptions;
}

/** Columns by tier: dagre orders each rank to keep crossings low, then each tier takes its own column (so unlinked
 *  facilities stand with their tier, not in the first rank) and a long column wraps into sub-columns of ROWS. */
function placeInColumns(cy: Core): void {
  const byColumn = new Map<number, ReturnType<Core["nodes"]>[number][]>();
  cy.nodes().forEach((n) => {
    const c = n.data("column") as number;
    byColumn.set(c, [...(byColumn.get(c) ?? []), n]);
  });
  let x = 0;
  for (const c of [...byColumn.keys()].sort((a, b) => a - b)) {
    const nodes = byColumn.get(c)!.sort((a, b) => a.position("y") - b.position("y"));
    const wraps = Math.ceil(nodes.length / ROWS);
    const per = Math.ceil(nodes.length / wraps);
    nodes.forEach((n, i) => n.position({ x: x + Math.floor(i / per) * SUB, y: ((i % per) - (per - 1) / 2) * ROW }));
    x += (wraps - 1) * SUB + COLUMN;
  }
}

export function runFacilityLayout(cy: Core, kind: LayoutChoice): void {
  cy.layout(layoutOptions(kind)).run();
  if (kind === "hierarchy") placeInColumns(cy);
  cy.fit(undefined, 24);
}

/** The facilities of the slice on a canvas without coordinates; laid out once per slice or layout, no animation. */
export default function FacilityGraph({ slice, exposureAvailable, selected, link, highlight, onSelect, onSelectLink, onClear, layout, onReady }: FacilityCanvasProps & {
  layout: LayoutChoice;
  onReady?: (cy: Core) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const handlers = useRef({ onSelect, onSelectLink, onClear });
  handlers.current = { onSelect, onSelectLink, onClear };
  const [hover, setHover] = useState<LinkHover | null>(null);
  const { theme } = useTheme();
  const links = useMemo(() => new Map(slice.supply.map((l) => [linkKey(l), l])), [slice]);
  const names = useMemo(() => new Map(slice.entities.map((e) => [entityKey(e), e])), [slice]);

  useEffect(() => {
    const headless = !canDraw();
    const cy = cytoscape({
      container: headless ? undefined : container.current,
      headless,
      elements: facilityElements(slice, exposureAvailable),
      style: stylesheet(),
      minZoom: 0.1,
      maxZoom: 3,
      wheelSensitivity: 0.3,
      boxSelectionEnabled: false,
      autoungrabify: false,
      selectionType: "single",
      textureOnViewport: true,
      hideEdgesOnViewport: slice.entities.length > 200,
    });
    runFacilityLayout(cy, layout);
    cy.on("tap", "node", (e) => handlers.current.onSelect(e.target.data("type"), e.target.data("nodeId")));
    cy.on("tap", 'edge[kind = "supply"]', (e) => handlers.current.onSelectLink(e.target.data("supplier"), e.target.data("site")));
    cy.on("tap", (e) => e.target === cy && handlers.current.onClear());
    cy.on("mouseover", "node", (e) => e.target.connectedEdges().addClass("hover"));
    cy.on("mouseout", "node", (e) => e.target.connectedEdges().removeClass("hover"));
    cy.on("mouseover", 'edge[kind = "supply"]', (e) => {
      const l = links.get(`${e.target.data("supplier")}>${e.target.data("site")}`);
      // the middle of the link on screen, from its two ends (the headless renderer has no edge midpoint)
      const a = e.target.source().renderedPosition();
      const b = e.target.target().renderedPosition();
      e.target.addClass("hover");
      if (l) setHover({ link: l, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
    });
    cy.on("mouseout", "edge", (e) => {
      e.target.removeClass("hover");
      setHover(null);
    });
    cyRef.current = cy;
    onReady?.(cy);
    return () => {
      cy.destroy();
      cyRef.current = null;
      setHover(null);
    };
  }, [slice, exposureAvailable, layout, links]); // eslint-disable-line react-hooks/exhaustive-deps

  // Select and centre the chosen facility or link without a new layout.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$(":selected").unselect();
    const target = selected ? cy.getElementById(selected) : link ? cy.getElementById(`L:${link}`) : null;
    if (target?.nonempty()) {
      target.select();
      if (selected) cy.center(target);
    }
  }, [selected, link, slice, layout]);

  // Fade what lies outside the highlight (the selection's chain, a part's facilities).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded");
    if (!highlight) return;
    cy.nodes().filter((n) => !highlight.has(n.id())).addClass("faded");
    cy.edges().filter((e) => !highlight.has(e.source().id()) || !highlight.has(e.target().id())).addClass("faded");
  }, [highlight, slice, layout]);

  useEffect(() => {
    cyRef.current?.style(stylesheet());
  }, [theme]);

  return (
    <div className="relative h-full w-full" data-testid="facility-graph">
      <div ref={container} className="h-full w-full" data-testid="graph-canvas" />
      <Button size="icon" variant="outline" className="absolute right-3 bottom-3 size-8" aria-label="Fit to view" onClick={() => cyRef.current?.fit(undefined, 24)}>
        <Maximize />
      </Button>
      {hover && <LinkTooltip hover={hover} names={names} />}
      {/* The same facilities as a list, for keyboard and screen-reader use. */}
      <ul className="sr-only" aria-label="Facilities in view">
        {slice.entities.map((e) => (
          <li key={entityKey(e)}>
            <button aria-pressed={entityKey(e) === selected} onClick={() => onSelect(e.type, e.id)}>
              {e.type} {e.name}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
