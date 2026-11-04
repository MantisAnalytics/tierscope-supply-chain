import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import * as f from "./fixtures";
import { jsonResponse, mockApi, renderApp } from "./utils";

const human = f.fieldValue(9, {
  method: "human",
  locked: true,
  source: { file: "letter.pdf", page: 2, quote: "recovery takes nine months", note: "buyer call 29.10", author: "ana", evidence_id: "ab".repeat(32) },
  label: "letter.pdf p. 2",
  quote: "recovery takes nine months",
  evidence_id: "ab".repeat(32),
  author: "ana",
});
const earlier = f.fieldValue(6, { active: false, status: "superseded", review: "superseded", confidence: 0.8, source: { file: "DMAuto.xlsx", sheet: "Production Sites", row: 3, column: "M" }, label: "DMAuto.xlsx Production Sites!M3" });

function api(site: unknown[] = [human, earlier]) {
  return mockApi({
    "GET /api/stats": f.stats(9),
    "GET /api/schema": f.schema,
    "GET /api/nodes/Supplier/S100228": f.supplierNode,
    "GET /api/exposure/Supplier/S100228": f.supplierExposure,
    "GET /api/nodes/Site/DMA-DE-01": f.siteNode(site),
    "GET /api/exposure/Site/DMA-DE-01": () => jsonResponse(422, { detail: "total assembly revenue is zero; load Assembly.revenue_eur" }),
  });
}

describe("Node panel with provenance", () => {
  it("Inspect a model-extracted value: quote, document with page and lines, confidence and method", async () => {
    api();
    renderApp("/?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Kessler Antriebe GmbH" });
    expect(within(panel).getByTestId("field-supplies_material")).toHaveTextContent("cast aluminium housings");
    await userEvent.click(within(panel).getByRole("button", { name: "Show sources of Supplies material" }));
    const candidates = within(panel).getByRole("list", { name: "Candidates of Supplies material" });
    expect(candidates).toHaveTextContent("“we deliver cast aluminium housings”");
    expect(candidates).toHaveTextContent("supplier_letter.pdf");
    expect(candidates).toHaveTextContent("page 2");
    expect(candidates).toHaveTextContent("lines 14–16");
    expect(within(candidates).getByLabelText(/confidence 0.82/)).toBeInTheDocument();
    expect(candidates).toHaveTextContent("model");
    // exposure with impacted assemblies and BOM paths
    await userEvent.click(within(panel).getByRole("button", { name: "BOM paths in DMP3552717" }));
    const paths = within(panel).getByRole("list", { name: "BOM paths in DMP3552717" });
    expect(paths).toHaveTextContent("DMC-2221›DMC-5010›DMC-9948");
    // every relation, collapsed until asked for
    expect(within(panel).queryByText("SUPPLIES")).not.toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: /All relations \(3\)/ }));
    expect(within(panel).getByText("SUPPLIES")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Gear housing" })).toBeInTheDocument();
  });

  it("Superseded value visible: the human value active and locked with author and evidence, the earlier one superseded", async () => {
    api();
    renderApp("/?sel=Site:DMA-DE-01");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Werk Stuttgart" });
    expect(within(panel).getByTestId("field-recovery_time_months")).toHaveTextContent("9");
    await userEvent.click(within(panel).getByRole("button", { name: "Show sources of Recovery time months" }));
    const [active, old] = within(within(panel).getByRole("list", { name: "Candidates of Recovery time months" })).getAllByRole("listitem");
    expect(active).toHaveTextContent("9");
    expect(active).toHaveTextContent("active");
    expect(active).toHaveTextContent("locked");
    expect(active).toHaveTextContent("person");
    expect(active).toHaveTextContent("ana");
    expect(active).toHaveTextContent("buyer call 29.10");
    expect(within(active).getByRole("link", { name: /evidence file/ })).toHaveAttribute("href", `/api/evidence/${"ab".repeat(32)}`);
    expect(old).toHaveTextContent("6");
    expect(old).toHaveTextContent("superseded");
    expect(old).toHaveTextContent("Production Sites!M3");
    expect(within(panel).getByText(/Exposure unavailable: no assembly revenue/)).toBeInTheDocument();
  });
});

describe("Node fields as a table", () => {
  it("Fields at a glance: twelve values as twelve one-line rows with name, value, confidence and state", async () => {
    const names = ["name", "address", "city", "postal_code", "country", "region", "latitude", "longitude", "tier", "risk_score_internal", "nda_signed", "contact_email"];
    const fields = Object.fromEntries(names.map((n, i) => [n, [f.fieldValue(n === "name" ? "Kessler Antriebe GmbH" : `v${i}`, { confidence: 0.95 })]]));
    fields.tier = [f.fieldValue(1, { locked: true, method: "human" }), f.fieldValue(2, { active: false, review: "superseded" })];
    fields.region = [f.fieldValue("Baden", { status: "flagged" })];
    mockApi(f.overviewApi({ "GET /api/nodes/Supplier/S100228": { ...f.supplierNode, fields } }));
    renderApp("/?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Kessler Antriebe GmbH" });
    const table = within(panel).getByRole("table", { name: "Fields" });
    const rows = table.querySelectorAll("tr[data-field]");
    expect(rows).toHaveLength(12);
    const city = within(table).getByTestId("field-city").closest("tr")!;
    expect(within(city).getAllByRole("cell")).toHaveLength(4); // name, value, confidence and state on one row
    expect(within(city).getByLabelText("confidence 0.95 (high)")).toHaveTextContent("0.95");
    expect(within(table).getByTestId("state-tier")).toContainElement(within(table).getByLabelText("locked"));
    expect(within(table).getByLabelText("1 other candidate")).toHaveTextContent("+1");
    expect(within(table).getByTestId("state-region")).toContainElement(within(table).getByLabelText("flagged"));
    expect(within(panel).queryByRole("list", { name: /Candidates of/ })).not.toBeInTheDocument(); // nothing expanded
    await userEvent.click(within(table).getByRole("button", { name: "Show sources of Tier" }));
    expect(within(table).getByRole("list", { name: "Candidates of Tier" })).toHaveTextContent("superseded");
  });

  it("Parts and risks of a supplier: figures, chain by tier, a parts table and a risks table", async () => {
    const network = {
      ...f.network,
      entities: [...f.network.entities, { ...f.network.entities[3], id: "DMA-PL-02", name: "Zakład Poznań", country: "Poland" }],
      supply_links: [...f.network.supply_links, { supplier: "S100228", site: "DMA-PL-02", parts: [f.network.supply_links[0].parts[1]] }],
    };
    mockApi(f.overviewApi({ "GET /api/network": network }));
    renderApp("/?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Kessler Antriebe GmbH" });
    const figures = within(panel).getByLabelText("Key figures");
    expect(figures).toHaveTextContent("Business Impact€18.4M");
    expect(figures).toHaveTextContent("Exposure41.20%");
    expect(figures).toHaveTextContent("Tier1");
    expect(figures).toHaveTextContent("CountryGermany");

    const chain = within(panel).getByRole("region", { name: "Supply chain" });
    expect(within(chain).getByRole("group", { name: "Tier 2" })).toHaveTextContent("Feinguss Weber");
    const sites = within(chain).getByRole("group", { name: "Sites" });
    expect(within(sites).getAllByRole("listitem").map((r) => r.textContent)).toEqual(["Werk Stuttgart2 parts", "Zakład Poznań1 part"]);

    const parts = within(within(panel).getByRole("table", { name: "Parts" })).getAllByRole("row");
    expect(parts).toHaveLength(4); // header and three part rows, highest spend first
    expect(parts[0]).toHaveTextContent("PartSiteSpendSSExposure");
    expect(parts[1]).toHaveTextContent("DMC-9948Gear housingWerk Stuttgart€1.2M●21.40%");

    const risks = within(within(panel).getByRole("table", { name: "Risks" })).getAllByRole("row");
    expect(risks).toHaveLength(3);
    expect(risks[1]).toHaveTextContent("Supplier regionGeographyEastern AsiaERP sourcing (2026-08-19 extract)2026-08-19");
    expect(risks[2]).toHaveTextContent("Credit ratingFinancialBB-");

    await userEvent.click(within(sites).getByRole("button", { name: "1 part" }));
    expect(await screen.findByRole("complementary", { name: "Link details" })).toHaveTextContent("Zakład Poznań");
  });

  it("No empty sections: a site without risks or sources shows no risks or sources section", async () => {
    mockApi(f.overviewApi({
      "GET /api/nodes/Site/DMA-DE-01": f.siteNode([f.fieldValue(6)]),
      "GET /api/exposure/Site/DMA-DE-01": { ...f.supplierExposure, type: "Site", id: "DMA-DE-01" },
    }));
    renderApp("/?sel=Site:DMA-DE-01");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Werk Stuttgart" });
    expect(within(panel).getByRole("table", { name: "Parts" })).toHaveTextContent("Kessler Antriebe GmbH");
    expect(within(panel).getByRole("region", { name: "Supply chain" })).toBeInTheDocument();
    expect(within(panel).queryByRole("region", { name: /Risks/ })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("region", { name: "Sources" })).not.toBeInTheDocument();
  });
});
