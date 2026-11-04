import { act, render } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  activeFilters,
  DEFAULT_VIEW,
  nodeAddress,
  parseView,
  serialiseView,
  useNetworkView,
  type NetworkView,
} from "@/features/network/view";

describe("View state in the address", () => {
  it("leaves every default out of the address", () => {
    expect(serialiseView(DEFAULT_VIEW)).toBe("");
    expect(parseView("")).toEqual(DEFAULT_VIEW);
    expect(parseView("?types=Supplier,Site&tiers=1")).toEqual(DEFAULT_VIEW);
  });

  it("round-trips a full view with readable separators", () => {
    const view: NetworkView = {
      view: "graph",
      sel: { type: "Supplier", id: "S100228" },
      link: { supplier: "S100228", site: "DMA-DE-01" },
      trail: [{ type: "Component", id: "DMC:9948" }, { type: "Link", id: "S100228>DMA-DE-01" }, { type: "Assembly", id: "DMP3552717" }],
      layout: "rings",
      types: ["Supplier"],
      tiers: [1, 2],
      country: ["Germany", "Czech Republic"],
      risk: true,
      ss: true,
      metric: "exposure",
      top: 10,
    };
    const search = serialiseView(view);
    expect(search).toContain("sel=Supplier:S100228");
    expect(search).toContain("link=S100228>DMA-DE-01");
    expect(search).toContain("trail=Component:DMC:9948,Link:S100228>DMA-DE-01,Assembly:DMP3552717");
    expect(search).toContain("country=Germany,Czech%20Republic");
    expect(parseView(search)).toEqual(view);
  });

  it("percent-encodes a comma inside a trail id and reads it back", () => {
    const view = { ...DEFAULT_VIEW, trail: [{ type: "RiskIndicator", id: "S1::Finance::Rating, outlook" }, { type: "Component", id: "C,2" }] };
    const search = serialiseView(view);
    expect(search).toBe("?trail=RiskIndicator:S1::Finance::Rating%2C%20outlook,Component:C%2C2");
    expect(parseView(search)).toEqual(view);
  });

  it("falls back to defaults for malformed values", () => {
    const v = parseView("?view=globe&lens=x&sel=nocolon&link=>x&focus=:a&trail=nocolon,%E0%A4%A&layout=grid&types=Truck&tiers=0,a&metric=cost&top=-3&risk=yes");
    expect(v).toEqual(DEFAULT_VIEW);
  });

  it("opens an earlier Parts lens address on the trail and drops its graph settings", () => {
    expect(parseView("?lens=parts&sel=Component:DMC-9948&focus=Component:DMC-9948&depth=2&rel=CONTAINS&pending=1")).toEqual({
      ...DEFAULT_VIEW,
      trail: [{ type: "Component", id: "DMC-9948" }],
    });
    expect(parseView("?lens=parts&focus=Assembly:A1&sel=Component:C1")).toEqual({
      ...DEFAULT_VIEW,
      trail: [{ type: "Assembly", id: "A1" }, { type: "Component", id: "C1" }],
    });
    expect(parseView("?lens=parts&focus=Supplier:S1")).toEqual({ ...DEFAULT_VIEW, sel: { type: "Supplier", id: "S1" } });
  });

  it("counts the filters that differ from the default", () => {
    expect(activeFilters(DEFAULT_VIEW)).toBe(0);
    expect(activeFilters({ ...DEFAULT_VIEW, sel: { type: "Site", id: "x" }, view: "graph" })).toBe(0);
    expect(activeFilters({ ...DEFAULT_VIEW, types: ["Supplier"], top: 10 })).toBe(2);
    expect(activeFilters(parseView("?tiers=1,2&country=Germany&risk=1&ss=1&metric=exposure"))).toBe(5);
  });

  it("selects sites and suppliers on the network and opens other nodes on the trail", () => {
    expect(nodeAddress("Supplier", "S100228")).toBe("/?sel=Supplier:S100228");
    expect(nodeAddress("Component", "DMC-9948")).toBe("/?trail=Component:DMC-9948");
  });
});

describe("useNetworkView", () => {
  function setup(path: string) {
    let api: ReturnType<typeof useNetworkView> | null = null;
    const seen: { search: string; nav: string }[] = [];
    function Probe() {
      api = useNetworkView();
      const { search } = useLocation();
      seen.push({ search, nav: useNavigationType() });
      return null;
    }
    render(
      <MemoryRouter initialEntries={[path]}>
        <Probe />
      </MemoryRouter>,
    );
    return { get: () => api!, last: () => seen[seen.length - 1] };
  }

  it("replaces the history entry for a selection or filter and pushes for a drill-in", () => {
    const t = setup("/?tiers=1,2");
    act(() => t.get()[1]({ sel: { type: "Supplier", id: "S1" } }));
    expect(t.last()).toEqual({ search: "?sel=Supplier:S1&tiers=1,2", nav: "REPLACE" });
    act(() => t.get()[1]({ trail: [{ type: "Component", id: "C1" }] }));
    expect(t.last()).toEqual({ search: "?sel=Supplier:S1&trail=Component:C1&tiers=1,2", nav: "PUSH" });
    act(() => t.get()[1]({ country: ["Germany"] }));
    expect(t.last().nav).toBe("REPLACE");
    expect(t.get()[0].trail).toEqual([{ type: "Component", id: "C1" }]);
  });
});
