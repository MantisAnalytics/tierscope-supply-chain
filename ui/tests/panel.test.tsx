import type { Core } from "cytoscape";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Network } from "@/api/client";
import { chainOf } from "@/features/network/chain";
import FacilityGraph from "@/features/network/FacilityGraph";
import { filterNetwork } from "@/features/network/filters";
import { DEFAULT_VIEW } from "@/features/network/view";
import * as f from "./fixtures";
import { mockApi, renderApp, renderWith, search } from "./utils";

const bom = Object.fromEntries(Object.entries(f.bomNodes).map(([id, n]) => [`GET /api/nodes/Component/${id}`, n]));

function api(extra: Record<string, unknown> = {}) {
  return mockApi(f.overviewApi({
    "GET /api/nodes/Component/DMC-9948": f.componentNode,
    "GET /api/exposure/Component/DMC-9948": f.componentExposure,
    "GET /api/nodes/Assembly/DMP3552717": f.assemblyNode,
    ...bom,
    ...extra,
  }));
}

const breadcrumb = () => screen.getByRole("navigation", { name: "Breadcrumb" });
const crumbs = () => within(breadcrumb()).getAllByRole("listitem").map((c) => c.textContent);

describe("Side panel stack", () => {
  it("Drill into a part and back: supplier, part, assembly, the supplier crumb, then Escape", async () => {
    api();
    renderApp("/?sel=Supplier:S100228");
    let panel = await screen.findByRole("complementary", { name: "Node details" });
    const parts = await within(panel).findByRole("table", { name: "Parts" });
    await userEvent.click(within(parts).getByRole("button", { name: /DMC-9948/ }));
    expect(search()).toBe("?sel=Supplier:S100228&trail=Component:DMC-9948");
    panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByRole("heading", { name: "Gear housing" })).toBeInTheDocument();

    await userEvent.click(within(await within(panel).findByTestId("exposure")).getByRole("button", { name: "DMP3552717" }));
    expect(search()).toBe("?sel=Supplier:S100228&trail=Component:DMC-9948,Assembly:DMP3552717");
    await screen.findByRole("tree", { name: "BOM of DMP3552717" });
    expect(crumbs()).toEqual(["Summary", "Kessler Antriebe GmbH", "DMC-9948", "DMP3552717"]);
    expect(within(breadcrumb()).getByText("DMP3552717")).toHaveAttribute("aria-current", "page");

    await userEvent.click(within(breadcrumb()).getByRole("button", { name: "Kessler Antriebe GmbH" }));
    expect(search()).toBe("?sel=Supplier:S100228");
    expect(await screen.findByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
    expect(crumbs()).toEqual(["Summary", "Kessler Antriebe GmbH"]);

    await userEvent.keyboard("{Escape}");
    expect(search()).toBe("");
    expect(await screen.findByRole("complementary", { name: "Summary" })).toBeInTheDocument();
  });

  it("selecting a facility from a part's panel starts a new trail from that facility", async () => {
    api();
    renderApp("/?sel=Supplier:S100228&trail=Component:DMC-9948");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await userEvent.click(within(await within(panel).findByRole("group", { name: "Delivered to" })).getByRole("button", { name: "Werk Stuttgart" }));
    expect(search()).toBe("?sel=Site:DMA-DE-01");
  });
});

describe("Assembly BOM", () => {
  it("shows the root component's parts for this assembly and loads each level when opened", async () => {
    const { calls } = api();
    renderApp("/?trail=Assembly:DMP3552717");
    const tree = await screen.findByRole("tree", { name: "BOM of DMP3552717" });
    // one level open by default: the root and its two parts in this assembly (not the other assembly's part)
    const level2 = await within(tree).findAllByRole("treeitem", { name: /DMC-50/ });
    expect(level2.map((i) => i.getAttribute("aria-label"))).toEqual(["DMC-5010", "DMC-5011"]);
    expect(within(tree).queryByRole("treeitem", { name: "DMC-7777" })).not.toBeInTheDocument();
    expect(level2[0]).toHaveTextContent("Gearbox");
    expect(calls.some((c) => c.path === "/api/nodes/Component/DMC-5010")).toBe(false); // not loaded until opened

    await userEvent.click(within(tree).getByRole("button", { name: "Expand DMC-5010" }));
    const level3 = await within(tree).findByRole("treeitem", { name: "DMC-9948" });
    expect(level3).toHaveAttribute("aria-level", "3");
    expect(level3).toHaveTextContent("Gear housing×2");
    await userEvent.click(within(tree).getByRole("button", { name: "Expand DMC-5011" }));
    expect(await within(tree).findByText("No parts below.")).toBeInTheDocument();

    await userEvent.click(within(level3).getByRole("button", { name: /^DMC-9948/ }));
    expect(search()).toBe("?trail=Assembly:DMP3552717,Component:DMC-9948");
  });

  it("highlights the facilities of the assembly's parts on the network", async () => {
    api();
    renderApp("/?trail=Assembly:DMP3552717");
    await screen.findByRole("tree", { name: "BOM of DMP3552717" });
    await waitFor(() => expect(screen.getByTestId("marker-Supplier:S523036")).toHaveAttribute("data-dim", "true"));
    expect(screen.getByTestId("marker-Supplier:S100228")).not.toHaveAttribute("data-dim");
    expect(screen.getByTestId("marker-Site:DMA-DE-01")).not.toHaveAttribute("data-dim");
  });
});

describe("Risk indicator", () => {
  it("shows its values and the supplier it belongs to", async () => {
    const id = "S100228::Geography::Supplier region";
    api({
      [`GET /api/nodes/RiskIndicator/${encodeURIComponent(id)}`]: {
        type: "RiskIndicator", id, referenced_only: false, sources: [],
        fields: { value: [f.fieldValue("Eastern Asia")], source_text: [f.fieldValue("ERP sourcing")] },
        edges: [{ id: 20, type: "HAS_RISK", direction: "in", key: [], identity: {}, fields: {}, sources: [], other: { type: "Supplier", id: "S100228", name: "Kessler Antriebe GmbH", values: {} } }],
      },
    });
    renderApp("/?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await userEvent.click(within(await within(panel).findByRole("table", { name: "Risks" })).getByRole("button", { name: /Supplier region/ }));
    expect(await within(panel).findByRole("heading", { name: "Supplier region" })).toBeInTheDocument();
    expect(within(panel).getByTestId("field-value")).toHaveTextContent("Eastern Asia");
    expect(within(panel).getByRole("group", { name: "Belongs to" })).toHaveTextContent("Kessler Antriebe GmbH");
    expect(crumbs()).toEqual(["Summary", "Kessler Antriebe GmbH", "Supplier region"]);
  });
});

describe("Supply chain of a selected facility", () => {
  it("Tier-1 supplier with hidden tier 2: shown and highlighted on the map under the tier-1 filter, others fade", async () => {
    api();
    renderApp("/?sel=Supplier:S100228");
    // T2-DM001 has no coordinates, so the map counts it; the panel lists it under tier 2
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(within(await within(panel).findByRole("region", { name: "Supply chain" })).getByRole("group", { name: "Tier 2" })).toHaveTextContent("Feinguss Weber");
    expect(screen.getByTestId("slice-count")).toHaveTextContent("4 facilities");
    expect(screen.getByTestId("marker-Supplier:S100228")).not.toHaveAttribute("data-dim");
    expect(screen.getByTestId("marker-Site:DMA-DE-01")).not.toHaveAttribute("data-dim");
    expect(screen.getByTestId("marker-Supplier:S523036")).toHaveAttribute("data-dim", "true");
  });

  it("fades facilities outside the chain in graph mode", () => {
    const net = f.network as unknown as Network;
    const highlight = chainOf(net, "Supplier:S100228").keys;
    let cy: Core | null = null;
    renderWith(
      <FacilityGraph
        slice={filterNetwork(net, DEFAULT_VIEW, highlight)}
        exposureAvailable
        selected="Supplier:S100228"
        link={null}
        highlight={highlight}
        layout="hierarchy"
        onSelect={() => {}}
        onSelectLink={() => {}}
        onClear={() => {}}
        onReady={(c) => (cy = c)}
      />,
    );
    const c = cy as unknown as Core;
    expect(c.getElementById("Supplier:T2-DM001").nonempty()).toBe(true); // tier 2 kept despite the tier-1 filter
    expect(c.getElementById("Supplier:T2-DM001").hasClass("faded")).toBe(false);
    expect(c.getElementById("Site:DMA-DE-01").hasClass("faded")).toBe(false);
    expect(c.getElementById("Supplier:S523036").hasClass("faded")).toBe(true);
    expect(c.getElementById("T:T2-DM001^S100228").hasClass("faded")).toBe(false);
  });
});
