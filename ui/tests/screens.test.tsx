import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import * as f from "./fixtures";
import { jsonResponse, mockApi, renderApp } from "./utils";

const suppliersSpec = {
  name: "x__suppliers_data",
  file: "x__suppliers_data.json",
  status: "draft",
  source_file: "DMAuto.xlsx",
  author: "model",
  model: "muse-spark-1.1",
  describes: "Supplier",
  region_kind: "data",
  sheets: ["Suppliers"],
  approved_by: null,
  approved_at: null,
  columns_by_role: { derived: 3, fact: 11, ignore: 4, key: 2 },
  below_threshold: 5,
};
const specBody = (status: string, conf: number) => ({
  metadata: { status },
  regions: [{
    id: "suppliers_data",
    sheet: "Suppliers",
    header_row: 1,
    confidence: conf,
    key_columns: { "Supplier ID*": { role: "key", confidence: 0.97, rationale: "Unique supplier identifier" } },
    columns: {
      "Supplier Name*": { role: "fact", target: "Supplier.name", confidence: 0.95 },
      "Business Exposure": { role: "derived", metric: "business_exposure_pct", entity: "Supplier", confidence: status === "approved" ? 1 : 0.85, rationale: "Formula result" },
    },
  }],
});

describe("Specs screen", () => {
  it("Approve a spec with derived columns: the spec shows as approved", async () => {
    localStorage.setItem("tierscope.author", "ana");
    let approved = false;
    const { calls } = mockApi({
      "GET /api/stats": f.stats(9),
      "GET /api/specs": () => [{ ...suppliersSpec, ...(approved ? { status: "approved", below_threshold: 0, approved_by: "ana" } : {}) }],
      "GET /api/specs/x__suppliers_data": () => ({
        summary: { ...suppliersSpec, ...(approved ? { status: "approved", below_threshold: 0, approved_by: "ana" } : {}) },
        spec: specBody(approved ? "approved" : "draft", approved ? 1 : 0.9),
      }),
      "POST /api/specs/x__suppliers_data/approve": () => {
        approved = true;
        return { summary: { ...suppliersSpec, status: "approved", below_threshold: 0, approved_by: "ana" }, spec: specBody("approved", 1) };
      },
    });
    renderApp("/specs");
    const detail = await screen.findByRole("article", { name: "Spec x__suppliers_data" });
    const derived = within(detail).getByRole("row", { name: /Business Exposure/ });
    expect(derived).toHaveTextContent("derived");
    expect(derived).toHaveTextContent("Supplier business_exposure_pct");
    expect(derived).toHaveTextContent("Formula result");
    expect(detail).toHaveTextContent("5 column(s) below the review threshold");
    await userEvent.click(within(detail).getByRole("button", { name: "Approve spec" }));
    await waitFor(() => expect(within(screen.getByRole("article", { name: "Spec x__suppliers_data" })).getByText("approved")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Approve spec" })).not.toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")!.headers["X-Author"]).toBe("ana");
  });
});

describe("Journal screen", () => {
  const entry = (id: number, action: string, summary: string, undone_by: number | null = null) => ({ id, ts: "2026-10-29T20:00:00+00:00", actor: "ana", action, summary, items: 1, undone_by });

  it("Undo from the journal: a new undo entry appears at the top", async () => {
    localStorage.setItem("tierscope.author", "ana");
    let undone = false;
    const { calls } = mockApi({
      "GET /api/stats": f.stats(9),
      "GET /api/journal": () =>
        undone
          ? [entry(4, "undo", "undo #3"), entry(3, "edit", "set Site DMA-DE-01.recovery_time_months = 9", 4), entry(1, "ingest", "ingest")]
          : [entry(3, "edit", "set Site DMA-DE-01.recovery_time_months = 9"), entry(1, "ingest", "ingest")],
      "POST /api/journal/3/undo": () => {
        undone = true;
        return { journal: entry(4, "undo", "undo #3") };
      },
    });
    renderApp("/journal");
    const list = await screen.findByRole("list", { name: "Journal entries" });
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("#3");
    expect(within(list).queryByRole("button", { name: "Undo #1" })).not.toBeInTheDocument(); // an ingest is not undone here
    await userEvent.click(within(list).getByRole("button", { name: "Undo #3" }));
    await waitFor(() => expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("undo #3"));
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("undone by #4");
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/journal/3/undo")).toBe(true);
  });

  it("shows the API's reason when an undo is refused, and loads older entries on demand", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const page = Array.from({ length: 50 }, (_, i) => entry(100 - i, "edit", `edit ${100 - i}`));
    mockApi({
      "GET /api/stats": f.stats(9),
      "GET /api/journal": (c: { url: string }) => (new URL(c.url, "http://x").searchParams.get("before") === "51" ? [entry(50, "ingest", "the first ingest")] : page),
      "POST /api/journal/100/undo": () => jsonResponse(409, { detail: { message: "a later change touched the same items", later: [101] } }),
    });
    renderApp("/journal");
    const list = await screen.findByRole("list", { name: "Journal entries" });
    await userEvent.click(within(list).getByRole("button", { name: "Undo #100" }));
    expect(await within(list).findByRole("alert")).toHaveTextContent("Undo refused: a later change touched the same items");
    await userEvent.click(screen.getByRole("button", { name: "Load older entries" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load older entries" })).not.toBeInTheDocument());
    expect(within(list).queryByText("the first ingest")).not.toBeInTheDocument(); // virtualised: not rendered out of view
    list.scrollTop = 51 * 40;
    fireEvent.scroll(list);
    expect(await within(list).findByText("the first ingest")).toBeInTheDocument();
  });
});
