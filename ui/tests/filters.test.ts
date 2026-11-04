import { describe, expect, it } from "vitest";
import type { Network } from "@/api/client";
import { filterNetwork, linkSummary, options, resolveMetric } from "@/features/network/filters";
import { DEFAULT_VIEW, type NetworkView } from "@/features/network/view";
import * as f from "./fixtures";

const net = f.network as unknown as Network;
const ids = (v: Partial<NetworkView>, keep?: string) => filterNetwork(net, { ...DEFAULT_VIEW, ...v }, keep ? new Set([keep]) : null).entities.map((e) => e.id).sort();

describe("Network filters", () => {
  it("shows sites and tier-1 suppliers by default", () => {
    expect(ids({})).toEqual(["DMA-DE-01", "S100228", "S523036"]);
    const unknownTier = { ...net, entities: [...net.entities, { ...net.entities[1], id: "S0", tier: null }] };
    expect(filterNetwork(unknownTier, DEFAULT_VIEW).entities.map((e) => e.id)).toContain("S0"); // no stated tier: tier 1
  });

  it("filters by type, tier, country, risk flags and single-source parts", () => {
    expect(ids({ types: ["Supplier"] })).toEqual(["S100228", "S523036"]);
    expect(ids({ types: ["Site"] })).toEqual(["DMA-DE-01"]);
    expect(ids({ tiers: [2] })).toEqual(["DMA-DE-01", "T2-DM001"]);
    expect(ids({ tiers: [1, 2], country: ["Poland", "Czechia"] })).toEqual(["S523036", "T2-DM001"]);
    expect(ids({ risk: true })).toEqual(["S100228"]);
    expect(ids({ ss: true })).toEqual(["DMA-DE-01", "S100228"]);
  });

  it("keeps the top N by Business Impact or by exposure", () => {
    expect(ids({ types: ["Supplier"], tiers: [1, 2], top: 2 })).toEqual(["S100228", "S523036"]);
    expect(ids({ tiers: [1, 2], top: 1, metric: "exposure" })).toEqual(["DMA-DE-01"]); // the site is most exposed
    expect(ids({ tiers: [1, 2], top: 1 })).toEqual(["S100228"]); // sites have no impact: ranked last
  });

  it("drops links whose end is hidden, and keeps tier links when both tiers show", () => {
    const both = filterNetwork(net, { ...DEFAULT_VIEW, tiers: [1, 2] });
    expect(both.supply).toHaveLength(1);
    expect(both.tier).toEqual([{ supplier: "T2-DM001", parent: "S100228" }]);
    const noSites = filterNetwork(net, { ...DEFAULT_VIEW, types: ["Supplier"], tiers: [1, 2] });
    expect(noSites.supply).toEqual([]);
    expect(noSites.tier).toHaveLength(1);
    expect(filterNetwork(net, DEFAULT_VIEW).tier).toEqual([]); // tier 2 hidden
  });

  it("keeps the selected facility visible whatever the filters", () => {
    expect(ids({ types: ["Site"] }, "Supplier:T2-DM001")).toEqual(["DMA-DE-01", "T2-DM001"]);
  });

  it("ranks by exposure when Business Impact is unavailable", () => {
    expect(resolveMetric(DEFAULT_VIEW, net)).toBe("impact");
    const pending = { ...net, entities: net.entities.map((e) => ({ ...e, impact_eur: null })) };
    expect(resolveMetric(DEFAULT_VIEW, pending)).toBe("exposure");
    expect(resolveMetric({ ...DEFAULT_VIEW, metric: "impact" }, pending)).toBe("impact");
  });

  it("offers the countries and tiers present, and sums a link's parts", () => {
    expect(options(net)).toEqual({ countries: ["Czechia", "Germany", "Poland"], tiers: [1, 2] });
    const s = linkSummary(net.supply_links[0]);
    expect({ ...s, top: s.top.map((p) => p.id) }).toEqual({ parts: 2, spend: 1_285_000, single: 1, top: ["DMC-9948", "DMC-1203"] });
  });
});
