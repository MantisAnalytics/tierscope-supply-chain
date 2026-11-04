import { describe, expect, it } from "vitest";
import type { Network } from "@/api/client";
import { chainOf, facilitiesOfParts, highlightOf } from "@/features/network/chain";
import { filterNetwork } from "@/features/network/filters";
import { DEFAULT_VIEW } from "@/features/network/view";

const facility = { country: "Germany", latitude: null, longitude: null, exposure_pct: 1, impact_eur: 1, risk_flags: 0, single_source_parts: 0 };
const supplier = (id: string, tier: number) => ({ ...facility, type: "Supplier", id, name: id, tier });
const site = (id: string) => ({ ...facility, type: "Site", id, name: id, tier: null });
const part = (id: string) => ({ id, description: null, annual_spend: 1, single_source: false, exposure_pct: 1 });

// T3 -> T2 -> A (tier 1) -> SITE1;  B (tier 1) -> SITE1, SITE2;  T2b -> B;  OTHER (tier 1) -> SITE3
const net = {
  version: 1,
  exposure_available: true,
  entities: [
    supplier("A", 1), supplier("B", 1), supplier("OTHER", 1), supplier("T2", 2), supplier("T2b", 2), supplier("T3", 3),
    site("SITE1"), site("SITE2"), site("SITE3"),
  ],
  supply_links: [
    { supplier: "A", site: "SITE1", parts: [part("P1")] },
    { supplier: "B", site: "SITE1", parts: [part("P2")] },
    { supplier: "B", site: "SITE2", parts: [part("P2"), part("P3")] },
    { supplier: "OTHER", site: "SITE3", parts: [part("P4")] },
  ],
  tier_links: [
    { supplier: "T2", parent: "A" },
    { supplier: "T3", parent: "T2" },
    { supplier: "T2b", parent: "B" },
  ],
} as unknown as Network;

const keys = (k: string) => [...chainOf(net, k).keys].sort();

describe("Supply chain of a selected facility", () => {
  it("Tier-1 supplier: its tier-2 and tier-3 suppliers and the site it supplies", () => {
    expect(keys("Supplier:A")).toEqual(["Site:SITE1", "Supplier:A", "Supplier:T2", "Supplier:T3"]);
    expect(chainOf(net, "Supplier:A").members.map((e) => e.id).sort()).toEqual(["SITE1", "T2", "T3"]);
  });

  it("Tier-2 supplier: upstream tier 3, the tier-1 supplier it feeds, and that supplier's sites", () => {
    expect(keys("Supplier:T2")).toEqual(["Site:SITE1", "Supplier:A", "Supplier:T2", "Supplier:T3"]);
    expect(keys("Supplier:T3")).toEqual(["Site:SITE1", "Supplier:A", "Supplier:T2", "Supplier:T3"]);
  });

  it("Site chain: its suppliers and everything upstream of them, nothing else", () => {
    expect(keys("Site:SITE1")).toEqual(["Site:SITE1", "Supplier:A", "Supplier:B", "Supplier:T2", "Supplier:T2b", "Supplier:T3"]);
    expect(keys("Site:SITE2")).toEqual(["Site:SITE2", "Supplier:B", "Supplier:T2b"]);
  });

  it("keeps the whole chain under the default tier-1 filter, with its tier links", () => {
    const slice = filterNetwork(net, DEFAULT_VIEW, chainOf(net, "Supplier:A").keys);
    expect(slice.entities.map((e) => e.id)).toEqual(expect.arrayContaining(["A", "T2", "T3", "SITE1"]));
    expect(slice.tier).toEqual([{ supplier: "T2", parent: "A" }, { supplier: "T3", parent: "T2" }]);
    expect(filterNetwork(net, DEFAULT_VIEW).entities.map((e) => e.id)).not.toContain("T2");
  });

  it("highlights the facilities of a part, an assembly's components and a link", () => {
    expect([...facilitiesOfParts(net, ["P2"])].sort()).toEqual(["Site:SITE1", "Site:SITE2", "Supplier:B"]);
    expect([...highlightOf(net, { type: "Assembly", id: "X" }, ["P1", "P4"])!].sort()).toEqual(["Site:SITE1", "Site:SITE3", "Supplier:A", "Supplier:OTHER"]);
    expect([...highlightOf(net, { type: "Link", id: "B>SITE2" })!]).toEqual(["Supplier:B", "Site:SITE2"]);
    expect(highlightOf(net, null)).toBeNull();
  });
});
