import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { rememberSearch } from "@/features/network/view";
import * as f from "./fixtures";
import { location, mockApi, renderApp, search } from "./utils";

describe("First load", () => {
  it("shows the Overview with logo, title, the navigation without Network and the pending proposal count", async () => {
    mockApi(f.overviewApi({ "GET /api/stats": f.stats(9) }));
    renderApp("/");
    expect(screen.getByRole("img", { name: "Mantis Analytics" })).toBeInTheDocument();
    expect(screen.getByText("Tierscope")).toBeInTheDocument();
    expect(screen.getByText("by Mantis Analytics")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent?.replace(/^Meta|\d+$/g, ""))).toEqual(["Overview", "Agent", "Reports", "Data"]);
    expect(await within(nav).findByLabelText("9 pending proposals")).toHaveTextContent("9");
    expect(await screen.findByRole("region", { name: "Key figures" })).toBeInTheDocument();
  });

  it("Search opens the node on the Overview: the supplier selected with its details in the side panel", async () => {
    mockApi(f.overviewApi());
    renderApp("/data/history");
    await userEvent.type(screen.getByRole("combobox", { name: /Search/ }), "Kessler");
    await userEvent.click(await screen.findByRole("option", { name: /Kessler Antriebe/ }));
    expect(location()).toBe("/");
    expect(search()).toBe("?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
  });
});

describe("Agent button", () => {
  it("Agent count: two plans ready and one overdue task show 3 on the Agent button", async () => {
    mockApi(f.overviewApi({ "GET /api/ops/summary": { ...f.opsSummary(), agent_needs: 3 } }));
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    const agent = within(nav).getByRole("link", { name: /Agent/ });
    expect(await within(agent).findByLabelText("3 disruptions waiting for you")).toHaveTextContent("3");
    expect(within(agent).getByRole("img", { name: "Meta" })).toBeInTheDocument();
    expect(agent).toHaveAttribute("href", "/agent");
  });

  it("renders as a gradient button with its label on the purple end, in the light and the dark theme", async () => {
    mockApi(f.overviewApi());
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const theme of ["light", "dark"]) {
      const agent = within(nav).getByRole("link", { name: /Agent/ });
      expect(agent).toHaveClass("bg-gradient-to-r", "from-agent-from", "to-agent-to", "text-agent-fg");
      expect(document.documentElement.classList.contains("dark")).toBe(theme === "dark");
      if (theme === "light") await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    }
  });
});

describe("View state in the address", () => {
  it("Come back to the same view: filters and selection are restored by the Overview link", async () => {
    rememberSearch("");
    mockApi(f.overviewApi({ "GET /api/agent/threads": [], "GET /api/ops/meta": f.opsMeta, "GET /api/ops/library": [] }));
    renderApp("/");
    await screen.findByTestId("marker-Supplier:S100228");
    await userEvent.click(screen.getByRole("button", { name: "Filters, 0 active" }));
    await userEvent.click(within(screen.getByRole("complementary", { name: "Filters" })).getByRole("radio", { name: "Exposure" }));
    await userEvent.click(within(screen.getByRole("region", { name: "Top risks" })).getByRole("button", { name: "Kessler Antriebe GmbH" }));
    expect(search()).toBe("?sel=Supplier:S100228&metric=exposure");
    const nav = screen.getByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByRole("link", { name: /Agent/ }));
    expect(location()).toBe("/agent");
    await userEvent.click(within(nav).getByRole("link", { name: "Overview" }));
    expect(location()).toBe("/");
    expect(search()).toBe("?sel=Supplier:S100228&metric=exposure");
    expect(await screen.findByRole("complementary", { name: "Node details" })).toBeInTheDocument();
  });

  it("Earlier Explorer address: a component opens in the side panel and its supplier and site are highlighted", async () => {
    const { calls } = mockApi(f.overviewApi({ "GET /api/nodes/Component/DMC-9948": f.componentNode, "GET /api/exposure/Component/DMC-9948": f.componentExposure }));
    renderApp("/explorer/Component/DMC-9948");
    expect(location()).toBe("/");
    expect(search()).toBe("?trail=Component:DMC-9948");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByRole("heading", { name: "Gear housing" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Parts" })).not.toBeInTheDocument(); // no lens switch
    expect(await screen.findByTestId("marker-Supplier:S100228")).not.toHaveAttribute("data-dim");
    expect(screen.getByTestId("marker-Site:DMA-DE-01")).not.toHaveAttribute("data-dim");
    expect(screen.getByTestId("marker-Supplier:S523036")).toHaveAttribute("data-dim", "true");
    expect(calls.some((c) => c.path === "/api/graph")).toBe(false);
  });

  it("Earlier Parts lens address opens the node on the trail", async () => {
    mockApi(f.overviewApi({ "GET /api/nodes/Component/DMC-9948": f.componentNode, "GET /api/exposure/Component/DMC-9948": f.componentExposure }));
    renderApp("/?lens=parts&sel=Component:DMC-9948&focus=Component:DMC-9948&depth=2");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByRole("heading", { name: "Gear housing" })).toBeInTheDocument();
  });

  it("Earlier Explorer address of a supplier selects it on the network", async () => {
    mockApi(f.overviewApi());
    renderApp("/explorer/Supplier/S100228");
    expect(search()).toBe("?sel=Supplier:S100228");
    expect(await screen.findByRole("complementary", { name: "Node details" })).toBeInTheDocument();
  });
});
