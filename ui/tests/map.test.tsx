import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Network } from "@/api/client";
import { filterNetwork } from "@/features/network/filters";
import { openingProjection, ringOffset, sharedGroups, worldBounds } from "@/features/network/mapGeometry";
import { TYPE_OUTLINE } from "@/features/network/marks";
import MapView from "@/features/network/MapView";
import { DEFAULT_VIEW } from "@/features/network/view";
import * as f from "./fixtures";

const net = f.network as unknown as Network;

export function renderMap(network: Network, extra: Partial<Parameters<typeof MapView>[0]> = {}) {
  const props = {
    slice: filterNetwork(network, DEFAULT_VIEW),
    exposureAvailable: true,
    selected: null,
    link: null,
    highlight: null as Set<string> | null,
    onSelect: vi.fn(),
    onSelectLink: vi.fn(),
    onClear: vi.fn(),
    onShowGraph: vi.fn(),
    ...extra,
  };
  render(<MapView {...props} />);
  return props;
}

describe("Site and supplier told apart", () => {
  it("gives a site and a supplier with the same exposure the same fill and a different shape and outline", () => {
    renderMap({ ...net, entities: net.entities.map((e) => ({ ...e, exposure_pct: 30 })) });
    const supplier = screen.getByTestId("marker-Supplier:S100228");
    const site = screen.getByTestId("marker-Site:DMA-DE-01");
    expect(site.getAttribute("fill")).toBe(supplier.getAttribute("fill"));
    expect(supplier.tagName).toBe("circle");
    expect(site.tagName).toBe("rect");
    expect(Number(site.getAttribute("rx"))).toBeGreaterThan(0);
    expect(supplier).toHaveAttribute("stroke", TYPE_OUTLINE.Supplier);
    expect(site).toHaveAttribute("stroke", TYPE_OUTLINE.Site);
  });
});

/** The zoom of the map group, from its transform. */
const zoomK = () => Number(/scale\(([\d.]+)\)/.exec(screen.getByTestId("countries").parentElement!.getAttribute("transform") ?? "")?.[1] ?? 1);
const onScreenRadius = (id: string) => Number(screen.getByTestId(`marker-${id}`).getAttribute("r")) * zoomK();
async function zoomIn(times: number) {
  for (let i = 0; i < times; i++) await userEvent.click(screen.getByRole("button", { name: "Zoom in" }));
}

// European facilities like the reference network's, two of them sharing one city's coordinates.
const europe = {
  ...net,
  entities: [
    ...net.entities,
    { ...net.entities[1], id: "S-HAM-1", name: "Hamburg Metall", latitude: 53.55, longitude: 9.99, impact_eur: 900_000 },
    { ...net.entities[1], id: "S-HAM-2", name: "Hamburg Guss", latitude: 53.55, longitude: 9.99, impact_eur: 300_000 },
  ],
} as Network;

describe("Map fills its area", () => {
  it("opens on the box of the placed facilities, padded, and leaves the empty ocean out", () => {
    const points: [number, number][] = [[9.2, 48.8], [21.0, 52.2], [9.18, 48.78], [-3.7, 40.4], [139.7, 35.7]];
    const [w, h] = [1440, 800];
    const p = openingProjection(points, w, h);
    const xy = points.map((pt) => p(pt)!);
    for (const [x, y] of xy) {
      expect(x).toBeGreaterThanOrEqual(w * 0.08 - 0.5);
      expect(x).toBeLessThanOrEqual(w * 0.92 + 0.5);
      expect(y).toBeGreaterThanOrEqual(h * 0.08 - 0.5);
      expect(y).toBeLessThanOrEqual(h * 0.92 + 0.5);
    }
    const [[wx0, wy0], [wx1, wy1]] = worldBounds(p);
    expect(wx1 - wx0).toBeGreaterThan(w * 1.2); // the whole globe is wider than the view: the Pacific is outside
    expect(wy1 - wy0).toBeGreaterThan(h);
    // Europe only: the opening view is much closer still
    const eu = openingProjection(points.slice(0, 4), w, h);
    expect(worldBounds(eu)[1][0] - worldBounds(eu)[0][0]).toBeGreaterThan(8 * w);
  });

  it("falls back to the land between 60°S and 85°N without placed facilities", () => {
    const p = openingProjection([], 1440, 800);
    for (const [lon, lat] of [[-180, 0], [180, 0], [0, -60], [0, 85]] as [number, number][]) {
      const [x, y] = p([lon, lat])!;
      expect(x).toBeGreaterThanOrEqual(1440 * 0.08 - 0.5);
      expect(x).toBeLessThanOrEqual(1440 * 0.92 + 0.5);
      expect(y).toBeGreaterThanOrEqual(800 * 0.08 - 0.5);
      expect(y).toBeLessThanOrEqual(800 * 0.92 + 0.5);
    }
  });

  it("draws into the container's pixel size, so there is no empty band, and reset returns to the opening view", async () => {
    renderMap(net);
    expect(screen.getByRole("img", { name: /World map/ })).toHaveAttribute("viewBox", "0 0 1280 800");
    await zoomIn(2);
    await waitFor(() => expect(zoomK()).toBeCloseTo(2.56));
    await userEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(screen.getByTestId("countries").parentElement).toHaveAttribute("transform", "translate(0,0) scale(1)");
  });
});

describe("Map navigation", () => {
  it("Zoom separates markers: on-screen sizes stay the same from k = 1 to k ≈ 20", async () => {
    renderMap(europe);
    const before = ["Supplier:S100228", "Supplier:S-HAM-1"].map(onScreenRadius);
    await zoomIn(7); // 1.6^7 ≈ 26.8, clamped by nothing below 48
    await waitFor(() => expect(Number(screen.getByTestId("marker-Supplier:S100228").getAttribute("r")) * zoomK()).toBeCloseTo(before[0], 5));
    expect(zoomK()).toBeGreaterThan(20);
    expect(onScreenRadius("Supplier:S-HAM-1")).toBeCloseTo(before[1], 5);
    expect(before[0]).toBeGreaterThan(before[1]); // relative size still means importance
  });

  it("zooms about the selected facility, so it stays in place even where the pan limits keep it off centre", async () => {
    const arctic = { ...europe, entities: [...europe.entities, { ...net.entities[1], id: "S-SVALBARD", name: "Svalbard Kull", latitude: 78.2, longitude: 15.6 }] } as Network;
    renderMap(arctic, { selected: "Supplier:S-SVALBARD" });
    const transform = () => screen.getByTestId("countries").parentElement!.getAttribute("transform") ?? "translate(0,0) scale(1)";
    const onScreen = () => {
      const [, tx, ty, k] = /translate\(([-\d.e]+),([-\d.e]+)\) scale\(([\d.e]+)\)/.exec(transform())!.map(Number);
      const m = screen.getByTestId("marker-Supplier:S-SVALBARD");
      return [Number(m.getAttribute("cx")) * k + tx, Number(m.getAttribute("cy")) * k + ty];
    };
    await waitFor(() => expect(transform()).toMatch(/translate/));
    const before = onScreen();
    expect(before[1]).toBeLessThan(400 - 50); // the pan limit keeps it above the centre (800 / 2)
    await zoomIn(5);
    await waitFor(() => expect(zoomK()).toBeGreaterThan(10));
    const after = onScreen();
    expect(after[0]).toBeCloseTo(before[0], 3);
    expect(after[1]).toBeCloseTo(before[1], 3);
  });

  it("zooms to 48 at most and out to 0.6", async () => {
    renderMap(net);
    await zoomIn(12);
    await waitFor(() => expect(zoomK()).toBe(48));
    for (let i = 0; i < 14; i++) await userEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    await waitFor(() => expect(zoomK()).toBeCloseTo(0.6));
  });

  it("Small marker on top: larger markers come first in drawing order", () => {
    renderMap(net);
    const big = screen.getByTestId("marker-Supplier:S100228");
    const small = screen.getByTestId("marker-Supplier:S523036");
    expect(Number(big.getAttribute("r"))).toBeGreaterThan(Number(small.getAttribute("r")));
    expect(big.compareDocumentPosition(small) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("loads the finer outlines only once zoomed in to 4", async () => {
    renderMap(net);
    const count = () => screen.getByTestId("countries").childElementCount;
    const coarse = count();
    await zoomIn(2); // 2.56
    await new Promise((r) => setTimeout(r, 50));
    expect(count()).toBe(coarse);
    await zoomIn(1); // 4.1
    await waitFor(() => expect(count()).toBeGreaterThan(coarse), { timeout: 5000 });
  });

  it("Shared coordinates: members fan out on a ring with a hairline from k = 6, each selectable, and say so", async () => {
    const props = renderMap(europe);
    const at = (id: string) => [screen.getByTestId(`marker-${id}`).getAttribute("cx"), screen.getByTestId(`marker-${id}`).getAttribute("cy")];
    expect(at("Supplier:S-HAM-1")).toEqual(at("Supplier:S-HAM-2")); // stacked below k = 6
    expect(screen.queryByTestId("ring-Supplier:S-HAM-1")).not.toBeInTheDocument();
    await zoomIn(4); // 6.55
    await waitFor(() => expect(at("Supplier:S-HAM-1")).not.toEqual(at("Supplier:S-HAM-2")));
    expect(screen.getByTestId("ring-Supplier:S-HAM-1")).toBeInTheDocument();
    expect(screen.queryByTestId("ring-Supplier:S100228")).not.toBeInTheDocument(); // alone at its point
    fireEvent.mouseEnter(screen.getByTestId("marker-Supplier:S-HAM-2"));
    expect(within(screen.getByRole("tooltip")).getByText("Same location as 1 other")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("marker-Supplier:S-HAM-2"));
    expect(props.onSelect).toHaveBeenCalledWith("Supplier", "S-HAM-2");
  });

  it("groups points within half a pixel and places members evenly on a ring that keeps them apart", () => {
    const groups = sharedGroups([
      { key: "a", x: 10, y: 10 },
      { key: "b", x: 10.3, y: 10.2 },
      { key: "c", x: 10, y: 10 },
      { key: "d", x: 12, y: 10 },
    ]);
    expect([...groups.keys()].sort()).toEqual(["a", "b", "c"]);
    expect(groups.get("c")).toMatchObject({ index: 2, size: 3 });
    const k = 8;
    const offsets = [0, 1, 2, 3].map((i) => ringOffset(i, 4, 10.5, k));
    for (const [dx, dy] of offsets) expect(Math.hypot(dx, dy) * k).toBeGreaterThanOrEqual(10.5 + 4 - 1e-9);
    const gap = Math.hypot(offsets[0][0] - offsets[1][0], offsets[0][1] - offsets[1][1]) * k;
    expect(gap).toBeGreaterThan(2 * 10.5); // neighbours on the ring do not overlap
  });
});
