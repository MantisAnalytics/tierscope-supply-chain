import type { Core } from "cytoscape";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Network } from "@/api/client";
import { ThemeProvider } from "@/app/theme";
import FacilityGraph, { stylesheet } from "@/features/network/FacilityGraph";
import { filterNetwork } from "@/features/network/filters";
import { DEFAULT_VIEW } from "@/features/network/view";
import * as f from "./fixtures";
import { mockApi, renderApp, search } from "./utils";

const net = f.network as unknown as Network;

function renderGraph(network: Network, tiers: number[], extra: Partial<Parameters<typeof FacilityGraph>[0]> = {}) {
  let cy: Core | null = null;
  const props = {
    slice: filterNetwork(network, { ...DEFAULT_VIEW, tiers }),
    exposureAvailable: true,
    selected: null,
    link: null,
    highlight: null as Set<string> | null,
    onSelect: vi.fn(),
    onSelectLink: vi.fn(),
    onClear: vi.fn(),
    layout: "hierarchy" as const,
    onReady: (c: Core) => (cy = c),
    ...extra,
  };
  const start = performance.now();
  render(<ThemeProvider><FacilityGraph {...props} /></ThemeProvider>);
  return { props, elapsed: performance.now() - start, cy: () => cy as unknown as Core };
}

/** A facility network shaped like the reference graph: 42 sites, 254 suppliers over four tiers. */
function referenceSized(): Network {
  const entities: Network["entities"] = [];
  const supply_links: Network["supply_links"] = [];
  const tier_links: Network["tier_links"] = [];
  const facility = { country: "Germany", latitude: null, longitude: null, exposure_pct: 1, impact_eur: 1, risk_flags: 0, single_source_parts: 0 };
  for (let i = 0; i < 42; i++) entities.push({ ...facility, type: "Site", id: `SITE${i}`, name: `Site ${i}`, tier: null });
  const tiers = [[1, 116], [2, 131], [3, 6], [4, 1]] as const;
  const byTier: Record<number, string[]> = {};
  for (const [tier, n] of tiers) {
    byTier[tier] = [];
    for (let i = 0; i < n; i++) {
      const id = `T${tier}-${i}`;
      byTier[tier].push(id);
      entities.push({ ...facility, type: "Supplier", id, name: `Supplier ${id}`, tier, impact_eur: (i % 17) * 1e5 });
      if (tier === 1) supply_links.push({ supplier: id, site: `SITE${i % 42}`, parts: [{ id: `P${i}`, description: null, annual_spend: 1, single_source: false, exposure_pct: 1 }] });
      else tier_links.push({ supplier: id, parent: byTier[tier - 1][i % byTier[tier - 1].length] });
    }
  }
  return { version: 1, exposure_available: true, entities, supply_links, tier_links };
}

describe("Facility graph", () => {
  it("draws only facilities, sized by the metric, with a risk halo and supply and tier links", () => {
    const { cy } = renderGraph(net, [1, 2]);
    const c = cy();
    expect(c.nodes().map((n) => n.id()).sort()).toEqual(["Site:DMA-DE-01", "Supplier:S100228", "Supplier:S523036", "Supplier:T2-DM001"]);
    expect(c.getElementById("Supplier:S100228").hasClass("risk")).toBe(true);
    expect(c.getElementById("Supplier:S100228").data("size")).toBeGreaterThan(c.getElementById("Supplier:S523036").data("size"));
    expect(c.edges().map((e) => e.data("kind")).sort()).toEqual(["supply", "tier"]);
    // material flows left to right: tier 2, tier 1, sites
    const x = (id: string) => c.getElementById(id).position("x");
    expect(x("Supplier:T2-DM001")).toBeLessThan(x("Supplier:S100228"));
    expect(x("Supplier:S100228")).toBeLessThan(x("Site:DMA-DE-01"));
  });

  it("Hover a link: a tooltip with the parts summary; a tap selects the link", () => {
    const { cy, props } = renderGraph(net, [1]);
    const edge = cy().getElementById("L:S100228>DMA-DE-01");
    act(() => void edge.emit("mouseover"));
    const tip = screen.getByRole("tooltip", { name: "Supply link" });
    expect(tip).toHaveTextContent("Parts2");
    expect(tip).toHaveTextContent("Single source1");
    expect(within(tip).getByRole("list", { name: "Top parts" })).toHaveTextContent("Gear housing");
    act(() => void edge.emit("mouseout"));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    act(() => void edge.emit("tap"));
    expect(props.onSelectLink).toHaveBeenCalledWith("S100228", "DMA-DE-01");
  });

  it("lays out the reference-sized network with every tier within a second", () => {
    const network = referenceSized();
    const { elapsed, cy } = renderGraph(network, [1, 2, 3, 4]);
    expect(cy().nodes().length).toBe(296);
    console.log(`facility graph, 296 nodes, every tier (dagre, headless): ${elapsed.toFixed(0)} ms`);
    expect(elapsed).toBeLessThan(1000);
    // tier 4 to tier 1 from left to right, then sites; long tiers wrap into sub-columns of at most 24
    const xs = (sel: string) => cy().nodes(sel).map((n) => n.position("x"));
    const span = (sel: string) => [Math.min(...xs(sel)), Math.max(...xs(sel))];
    const order = ['[id ^= "Supplier:T4"]', '[id ^= "Supplier:T3"]', '[id ^= "Supplier:T2"]', '[id ^= "Supplier:T1"]', '[type = "Site"]'].map(span);
    for (let i = 1; i < order.length; i++) expect(order[i][0]).toBeGreaterThan(order[i - 1][1]);
    const ys = new Set(cy().nodes('[id ^= "Supplier:T1"]').map((n) => n.position("y")));
    expect(ys.size).toBeLessThanOrEqual(24);
  });

  it("Idle app uses no CPU: the layout runs once, with no animation or movement afterwards", async () => {
    const { cy } = renderGraph(referenceSized(), [1, 2, 3, 4]);
    expect(cy().animated()).toBe(false);
    const before = cy().nodes().map((n) => ({ ...n.position() }));
    await new Promise((r) => setTimeout(r, 100));
    expect(cy().animated()).toBe(false);
    expect(cy().nodes().map((n) => ({ ...n.position() }))).toEqual(before);
  });

  it("Selection survives a mode switch: the supplier selected on the map is selected in the graph", async () => {
    mockApi(f.overviewApi());
    renderApp("/?sel=Supplier:S100228");
    expect(await screen.findByTestId("marker-Supplier:S100228")).toHaveAttribute("data-selected", "true");
    expect(screen.queryByTestId("facility-graph")).not.toBeInTheDocument(); // graph code not used in map mode
    await userEvent.click(screen.getByRole("radio", { name: "Graph" }));
    expect(search()).toBe("?view=graph&sel=Supplier:S100228");
    const list = await screen.findByRole("list", { name: "Facilities in view" });
    expect(within(list).getByRole("button", { name: /Kessler Antriebe GmbH/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("complementary", { name: "Node details" })).toBeInTheDocument();
  });
});

describe("Site and supplier told apart in the graph", () => {
  it("draws the same fill with a different shape and outline", () => {
    const same = { ...net, entities: net.entities.map((e) => ({ ...e, exposure_pct: 30 })) } as Network;
    const { cy } = renderGraph(same, [1, 2]);
    expect(cy().getElementById("Site:DMA-DE-01").data("fill")).toBe(cy().getElementById("Supplier:S100228").data("fill"));
    // headless Cytoscape computes no style, so read the rules
    const rule = (selector: string) => (stylesheet() as { selector: string; style: Record<string, unknown> }[]).find((r) => r.selector === selector)!.style;
    expect(rule("node").shape).toBe("ellipse");
    expect(rule('node[type = "Site"]').shape).toBe("round-rectangle");
    expect(rule('node[type = "Site"]')["border-color"]).not.toBe(rule("node")["border-color"]);
  });
});
