import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import * as f from "./fixtures";
import { mockApi, renderApp, search } from "./utils";

const summary = () => screen.findByRole("complementary", { name: "Summary" });

describe("Overview before revenue is accepted", () => {
  it("shows exposure figures as unavailable with a link to the revenue proposal, not as zero", async () => {
    mockApi(f.overviewApi({ "GET /api/stats": f.stats(9), "GET /api/overview": f.overviewPending, "GET /api/network": { ...f.network, exposure_available: false } }));
    renderApp("/");
    const kpis = await screen.findByRole("region", { name: "Key figures" });
    expect(within(kpis).getByText("Suppliers").nextSibling).toHaveTextContent("254");
    expect(within(kpis).getByText("Single-source components").nextSibling).toHaveTextContent("85");
    expect(within(kpis).getByText("Total assembly revenue").nextSibling).toHaveTextContent("—");
    expect(within(kpis).queryByText(/€0/)).not.toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Open proposal #8" });
    expect(links[0]).toHaveAttribute("href", "/data/review/8");
    expect(await screen.findByTestId("exposure-unavailable")).toHaveTextContent("Unavailable: revenue awaits review");
    expect(screen.getByTestId("pending-total")).toHaveTextContent("9");
    expect(screen.queryByText("Latest changes")).not.toBeInTheDocument(); // the journal stays in Data → History
  });
});

describe("Overview", () => {
  it("shows the key figures, the network and the company summary once revenue is accepted", async () => {
    mockApi(f.overviewApi());
    renderApp("/");
    const kpis = await screen.findByRole("region", { name: "Key figures" });
    expect(within(kpis).getByText("Total assembly revenue").nextSibling).toHaveTextContent("€2,400M");
    expect(await screen.findByTestId("marker-Supplier:S100228")).toBeInTheDocument();
    const panel = await summary();
    expect(within(panel).getByText("€18.4M")).toHaveClass("text-risk");
    await userEvent.click(within(panel).getByRole("tab", { name: "Sites by exposure" }));
    expect(within(panel).getByRole("button", { name: "Werk Stuttgart" })).toBeInTheDocument();
    expect(within(panel).getByText("55.0%")).toBeInTheDocument();
  });

  it("Active disruption on the Overview: listed with its revenue at risk and a link to its thread in Agent", async () => {
    mockApi(f.overviewApi({ "GET /api/ops/summary": f.opsSummary([{ ...f.fire, thread_id: 7 }]) }));
    renderApp("/");
    const panel = await summary();
    const link = await within(panel).findByRole("link", { name: /Fire at Kessler Antriebe/ });
    expect(link).toHaveAttribute("href", "/agent/t/7");
    expect(link).toHaveTextContent("€33.9M at risk");
  });

  it("Top risk opens its node: selected on the network with its details in the side panel", async () => {
    mockApi(f.overviewApi());
    renderApp("/?tiers=1,2");
    const risks = await screen.findByRole("region", { name: "Top risks" });
    await userEvent.click(within(risks).getByRole("button", { name: "Kessler Antriebe GmbH" }));
    expect(search()).toBe("?sel=Supplier:S100228&tiers=1,2"); // filters kept
    const details = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(details).findByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
    expect(screen.getByTestId("marker-Supplier:S100228")).toHaveAttribute("data-selected", "true");
  });

  it("Back to summary: Escape, the back control or a click on empty space clears the selection", async () => {
    mockApi(f.overviewApi());
    renderApp("/?sel=Supplier:S100228");
    await screen.findByRole("complementary", { name: "Node details" });
    await userEvent.keyboard("{Escape}");
    expect(await summary()).toBeInTheDocument();
    expect(search()).toBe("");

    fireEvent.click(await screen.findByTestId("marker-Supplier:S100228"));
    const details = await screen.findByRole("complementary", { name: "Node details" });
    await userEvent.click(within(details).getByRole("button", { name: "Summary" }));
    expect(await summary()).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("marker-Site:DMA-DE-01"));
    await screen.findByRole("complementary", { name: "Node details" });
    fireEvent.click(screen.getByTestId("countries").firstElementChild!);
    expect(await summary()).toBeInTheDocument();
  });
});

describe("Map mode", () => {
  it("Offline map: country shapes and all markers render from bundled data, only the local API is called", async () => {
    const { calls } = mockApi(f.overviewApi());
    renderApp("/?tiers=1,2");
    await screen.findByTestId("marker-Supplier:S100228");
    expect(screen.getByTestId("countries").querySelectorAll("path").length).toBeGreaterThan(150);
    for (const e of f.network.entities.filter((e) => e.latitude !== null)) expect(screen.getByTestId(`marker-${e.type}:${e.id}`)).toBeInTheDocument();
    expect(screen.getByTestId("risk-Supplier:S100228")).toBeInTheDocument(); // risk marker
    expect(calls.every((c) => c.url.startsWith("/api/"))).toBe(true); // no external host (tiles, fonts, CDNs)
  });

  it("says how many facilities have no coordinates and offers the graph", async () => {
    mockApi(f.overviewApi());
    renderApp("/?tiers=1,2");
    const note = await screen.findByTestId("missing-coordinates");
    expect(note).toHaveTextContent("1 facility has no coordinates.");
    await userEvent.click(within(note).getByRole("button", { name: "Show graph" }));
    expect(search()).toBe("?view=graph&tiers=1,2");
    expect(await screen.findByTestId("facility-graph")).toBeInTheDocument();
  });

  it("Data without coordinates: opens in graph mode, and the map says none can be placed", async () => {
    const placeless = { ...f.network, entities: f.network.entities.map((e) => ({ ...e, latitude: null, longitude: null })) };
    mockApi(f.overviewApi({ "GET /api/network": placeless }));
    renderApp("/");
    const list = await screen.findByRole("list", { name: "Facilities in view" });
    expect(within(list).getAllByRole("button")).toHaveLength(3); // sites and tier-1 suppliers
    await userEvent.click(screen.getByRole("radio", { name: "Map" }));
    expect(await screen.findByTestId("missing-coordinates")).toHaveTextContent("None of the 3 facilities has coordinates.");
  });
});

describe("Network filters", () => {
  it("Top suppliers by impact: only suppliers, top N, the count on the closed filter button", async () => {
    mockApi(f.overviewApi());
    renderApp("/?tiers=1,2");
    await screen.findByTestId("marker-Supplier:S100228");
    expect(screen.queryByRole("complementary", { name: "Filters" })).not.toBeInTheDocument(); // Closed filter panel
    await userEvent.click(screen.getByRole("button", { name: "Filters, 1 active" }));
    const drawer = screen.getByRole("complementary", { name: "Filters" });
    await userEvent.click(within(drawer).getByRole("button", { name: "Sites" }));
    await userEvent.click(within(drawer).getByRole("radio", { name: "Top 5" }));
    expect(search()).toBe("?types=Supplier&tiers=1,2&top=5");
    expect(screen.queryByTestId("marker-Site:DMA-DE-01")).not.toBeInTheDocument();
    expect(screen.getByTestId("slice-count")).toHaveTextContent("3 facilities · 0 supply links"); // link dropped with its site
    await userEvent.click(within(drawer).getByRole("button", { name: "Close filters" }));
    expect(screen.queryByRole("complementary", { name: "Filters" })).not.toBeInTheDocument();
    expect(screen.getByTestId("filter-count")).toHaveTextContent("3");
  });

  it("Slice summary: filtering to one country shows its facilities and the top five", async () => {
    mockApi(f.overviewApi());
    renderApp("/");
    await screen.findByTestId("marker-Supplier:S100228");
    await userEvent.click(screen.getByRole("button", { name: "Filters, 0 active" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Countries" })).getByRole("checkbox", { name: "Germany" }));
    const slice = await screen.findByRole("region", { name: "Visible slice" });
    expect(within(slice).getByTestId("slice-suppliers")).toHaveTextContent("1");
    expect(within(slice).getByTestId("slice-sites")).toHaveTextContent("1");
    expect(within(slice).getByTestId("slice-single")).toHaveTextContent("1");
    const top = within(slice).getByRole("list", { name: "Top five in view" });
    expect(within(top).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Kessler Antriebe GmbH"),
      expect.stringContaining("Werk Stuttgart"),
    ]);
    await userEvent.click(within(top).getByRole("button", { name: "Kessler Antriebe GmbH" }));
    expect(search()).toBe("?sel=Supplier:S100228&country=Germany");
  });
});

describe("Supply link details", () => {
  it("Hover a link on the map, select it, open one of its parts and then an assembly that uses it", async () => {
    mockApi(f.overviewApi({
      "GET /api/nodes/Component/DMC-9948": f.componentNode,
      "GET /api/exposure/Component/DMC-9948": f.componentExposure,
      "GET /api/nodes/Assembly/DMP3552717": f.assemblyNode,
      ...Object.fromEntries(Object.entries(f.bomNodes).map(([id, n]) => [`GET /api/nodes/Component/${id}`, n])),
    }));
    renderApp("/");
    const link = await screen.findByTestId("link-S100228>DMA-DE-01");
    fireEvent.mouseEnter(link, { clientX: 100, clientY: 80 });
    const tip = screen.getByRole("tooltip", { name: "Supply link" });
    expect(tip).toHaveTextContent("Kessler Antriebe GmbH → Werk Stuttgart");
    expect(tip).toHaveTextContent("Parts2");
    expect(tip).toHaveTextContent("Annual spend€1.3M");
    expect(tip).toHaveTextContent("Single source1");
    expect(within(tip).getByRole("list", { name: "Top parts" })).toHaveTextContent("Gear housing");
    fireEvent.mouseLeave(link);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    fireEvent.click(link);
    expect(search()).toBe("?link=S100228>DMA-DE-01");
    const panel = await screen.findByRole("complementary", { name: "Link details" });
    const rows = within(within(panel).getByRole("table", { name: "Parts on this link" })).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("DMC-9948");
    expect(rows[1]).toHaveTextContent("21.40%");
    await userEvent.click(within(rows[1]).getByRole("button"));
    expect(search()).toBe("?link=S100228>DMA-DE-01&trail=Component:DMC-9948");
    let details = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(details).findByRole("heading", { name: "Gear housing" })).toBeInTheDocument();
    const crumbs = () => within(screen.getByRole("navigation", { name: "Breadcrumb" })).getAllByRole("listitem").map((c) => c.textContent);
    expect(crumbs()).toEqual(["Summary", "Kessler Antriebe GmbH → Werk Stuttgart", "DMC-9948"]);
    // where the part comes from and goes to
    expect(within(details).getByRole("group", { name: "Supplied by" })).toHaveTextContent("Kessler Antriebe GmbH");
    expect(within(details).getByRole("group", { name: "Delivered to" })).toHaveTextContent("Werk Stuttgart");
    // used in, with BOM paths, the assembly pushed onto the trail
    const usedIn = await within(details).findByTestId("exposure");
    expect(usedIn).toHaveTextContent("Used in (1)");
    await userEvent.click(within(usedIn).getByRole("button", { name: "DMP3552717" }));
    expect(search()).toBe("?link=S100228>DMA-DE-01&trail=Component:DMC-9948,Assembly:DMP3552717");
    details = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(details).findByRole("tree", { name: "BOM of DMP3552717" })).toBeInTheDocument();
    expect(crumbs()).toEqual(["Summary", "Kessler Antriebe GmbH → Werk Stuttgart", "DMC-9948", "DMP3552717"]);
    // back to the link
    await userEvent.click(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("button", { name: "Kessler Antriebe GmbH → Werk Stuttgart" }));
    expect(search()).toBe("?link=S100228>DMA-DE-01");
    expect(await screen.findByRole("complementary", { name: "Link details" })).toBeInTheDocument();
  });
});
