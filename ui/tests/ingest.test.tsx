import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import IngestPage from "@/features/ingest/IngestPage";
import { jsonResponse, mockApi, renderWith } from "./utils";

// The screen renders on its own (it is a tab of the Data screen); the whole app is not loaded.
vi.mock("@/app/App", () => ({ App: () => null }));

const llm = (calls = 0, cost = 0) => ({ calls, cache_hits: 0, input_tokens: 0, output_tokens: 0, cost_usd: cost });
const load = (applied: number, proposals: number[] = []) => ({
  loaded: applied, applied, proposed: proposals.length * 10, unchanged: 0, dropped: 0, flagged: 0,
  proposals_created: proposals, proposals_updated: [], journal_id: 5,
});

const tableReport = (file: string, extra: Record<string, unknown> = {}) => ({
  kind: "table", file, run_id: "r1", encoding: null,
  regions: [
    { name: "suppliers_data", sheet: "Suppliers", kind: "data", source: "reused", problems: [], repair_rounds: 0, excluded_rows: [] },
    { name: "sites_data", sheet: "Production Sites", kind: "data", source: "reused", problems: [], repair_rounds: 0, excluded_rows: [] },
  ],
  ignored: { ReadMe: "instructions" }, text_fields: [], errors: [], load: load(0), llm: llm(), ...extra,
});

const totals = (t: Partial<Record<string, number>> = {}) => ({
  files: 0, queued: 0, running: 0, done: 0, failed: 0, skipped: 0, facts_applied: 0, facts_proposed: 0,
  proposals_created: 0, excluded_rows: 0, llm_calls: 0, cost_usd: 0, ...t,
});

const job = (id: string, status: string, files: unknown[], t: Partial<Record<string, number>>, extra: Record<string, unknown> = {}) => {
  const state = { files, current: null, totals: totals(t) };
  return {
    id, kind: "ingest", status, title: `${files.length} files: …`, created_at: "2026-10-31T10:00:00+00:00",
    updated_at: "2026-10-31T10:00:05+00:00", progress: state, result: status === "done" ? state : null, error: null, ...extra,
  };
};

describe("Ingest screen", () => {
  it("Re-upload a known layout with stored specs only: reused specs, zero LLM calls, no new proposals", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const done = job("j1", "done", [{ name: "DMAuto.xlsx", kind: "table", status: "done", report: tableReport("DMAuto.xlsx") }],
      { files: 1, done: 1 }, { title: "1 file: DMAuto.xlsx" });
    let started = false;
    const { calls } = mockApi({
      "GET /api/ingest/jobs": () => (started ? [done] : []),
      "POST /api/ingest/batch": () => {
        started = true;
        return done;
      },
      "GET /api/ingest/jobs/j1": done,
    });
    renderWith(<IngestPage />);
    expect(await screen.findByText("No batches yet.")).toBeInTheDocument();
    const file = new File(["xlsx"], "DMAuto.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    await userEvent.upload(screen.getByTestId("file-input"), file);
    const reuse = screen.getByLabelText("Stored specs only (no model calls)");
    expect(reuse).not.toBeChecked();
    await userEvent.click(reuse);
    await userEvent.click(screen.getByRole("button", { name: "Ingest" }));

    const batch = await screen.findByLabelText("Batch");
    const files = within(batch).getByRole("list", { name: "Files" });
    await userEvent.click(within(files).getByRole("button", { name: "Show report for DMAuto.xlsx" }));
    const report = within(batch).getByLabelText("Ingest report DMAuto.xlsx");
    expect(within(report).getByTestId("llm-calls")).toHaveTextContent("0");
    expect(within(report).getByTestId("no-new-proposals")).toBeInTheDocument();
    expect(within(within(report).getByRole("table", { name: "Regions" })).getAllByText("reused")).toHaveLength(2);
    expect(within(batch).getByTestId("batch-llm")).toHaveTextContent("0 calls, $0.0000");

    const post = calls.find((c) => c.method === "POST")!;
    const form = post.body as FormData;
    expect(post.path).toBe("/api/ingest/batch");
    expect(form.get("reuse_only")).toBe("true");
    expect((form.getAll("files") as File[]).map((f) => f.name)).toEqual(["DMAuto.xlsx"]);
    expect(await screen.findByRole("button", { name: /1 file: DMAuto.xlsx/ })).toBeInTheDocument(); // in recent batches
  });

  it("Drop a zip: live status per file, then each file's report and the totals", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const names = ["exports.zip/erp/components.xlsx", "exports.zip/erp/bom.csv", "exports.zip/erp/contract.txt"];
    const queued = job("j2", "queued", names.map((name, i) => ({ name, kind: i < 2 ? "table" : "document", status: "queued" })),
      { files: 3, queued: 3 });
    const running = job("j2", "running", [
      { name: names[0], kind: "table", status: "done", report: tableReport(names[0]) },
      { name: names[1], kind: "table", status: "running" },
      { name: names[2], kind: "document", status: "queued" },
    ], { files: 3, done: 1, running: 1, queued: 1, facts_applied: 800 });
    running.progress.current = names[1] as unknown as null;
    const components = tableReport(names[0], {
      regions: [{ name: "export_components", sheet: "Export", kind: "data", source: "generated", problems: [], repair_rounds: 0,
        excluded_rows: [{ sheet: "Export", row: 458, label: "TOTAL", reason: "summary row under the table" }] }],
      load: load(800, [10, 11]), llm: llm(2, 0.0126),
    });
    const finished = job("j2", "done", [
      { name: names[0], kind: "table", status: "done", report: components },
      { name: names[1], kind: "table", status: "failed", error: "no stored spec in mappings/generated matches sheet(s): None" },
      { name: names[2], kind: "document", status: "done", report: { ...tableReport(names[2]), kind: "document", regions: undefined, chunks: 1, extracted: 4, rejected: [], unresolved: [] } },
    ], { files: 3, done: 2, failed: 1, facts_applied: 812, facts_proposed: 20, proposals_created: 2, excluded_rows: 1, llm_calls: 4, cost_usd: 0.02 });
    let reads = 0;
    let started = false;
    mockApi({
      "GET /api/ingest/jobs": () => (started ? [reads > 1 ? finished : running] : []),
      "POST /api/ingest/batch": () => {
        started = true;
        return queued;
      },
      "GET /api/ingest/jobs/j2": () => (++reads > 1 ? finished : running),
    });
    renderWith(<IngestPage />);
    const zone = await screen.findByRole("button", { name: "Drop files here or choose them" });
    fireEvent.drop(zone, { dataTransfer: { files: [new File(["PK"], "exports.zip"), new File(["x"], "photo.png")] } });
    expect(within(screen.getByRole("list", { name: "Chosen files" })).getAllByRole("listitem")).toHaveLength(1); // the png is refused
    await userEvent.click(screen.getByLabelText("Stored specs only (no model calls)"));
    await userEvent.click(screen.getByRole("button", { name: "Ingest" }));

    const batch = await screen.findByLabelText("Batch");
    await waitFor(() => expect(within(batch).getByRole("status")).toHaveTextContent(`Working on ${names[1]}`));
    expect(within(within(batch).getByRole("listitem", { name: names[1] })).getByText("running")).toBeInTheDocument();
    expect(within(batch).getByTestId("files-done")).toHaveTextContent("1 / 3");

    await waitFor(() => expect(within(batch).getByTestId("files-done")).toHaveTextContent("2 / 3"), { timeout: 4000 });
    expect(within(batch).queryByRole("status")).not.toBeInTheDocument();
    const failed = within(batch).getByRole("listitem", { name: names[1] });
    expect(within(failed).getByText("failed")).toBeInTheDocument();
    expect(failed).toHaveTextContent("no stored spec");
    expect(batch).toHaveTextContent("1 file failed; the others were ingested.");
    expect(within(batch).getByLabelText("Batch totals")).toHaveTextContent("812");
    expect(within(batch).getByTestId("batch-llm")).toHaveTextContent("4 calls, $0.0200");

    await userEvent.click(within(batch).getByRole("button", { name: `Show report for ${names[0]}` }));
    const report = within(batch).getByLabelText(`Ingest report ${names[0]}`);
    expect(within(report).getByRole("link", { name: "#10" })).toHaveAttribute("href", "/data/review/10");
    expect(within(report).getByTestId("excluded-rows")).toHaveTextContent("Export row 458 (“TOTAL”)");
    expect(report).toHaveTextContent("$0.0126");
  });

  it("lists recent batches after a reload and opens an older one", async () => {
    const newer = job("j4", "done", [{ name: "a.csv", kind: "table", status: "done", report: tableReport("a.csv") }], { files: 1, done: 1 }, { title: "1 file: a.csv" });
    const older = job("j3", "failed", [{ name: "b.csv", kind: "table", status: "queued" }], { files: 1, queued: 1 },
      { title: "1 file: b.csv", error: "the server stopped before the job finished" });
    const { calls } = mockApi({ "GET /api/ingest/jobs": [newer, older], "GET /api/ingest/jobs/j4": newer, "GET /api/ingest/jobs/j3": older });
    renderWith(<IngestPage />);
    const recent = await screen.findByLabelText("Recent batches");
    expect(within(recent).getAllByRole("button").map((b) => b.textContent)).toEqual([expect.stringContaining("a.csv"), expect.stringContaining("b.csv")]);
    expect(await screen.findByRole("listitem", { name: "a.csv" })).toBeInTheDocument(); // the newest opens by itself
    await userEvent.click(within(recent).getByRole("button", { name: /b\.csv/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the server stopped before the job finished");
    expect(calls.some((c) => c.path === "/api/ingest/jobs/j3")).toBe(true);
  });

  it("shows why a batch was refused", async () => {
    localStorage.setItem("tierscope.author", "ana");
    mockApi({
      "GET /api/ingest/jobs": [],
      "POST /api/ingest/batch": () => jsonResponse(422, { detail: "the batch is larger than the limit of 200 MB uncompressed" }),
    });
    renderWith(<IngestPage />);
    await userEvent.upload(await screen.findByTestId("file-input"), [new File(["a"], "a.zip"), new File(["b"], "b.csv")]);
    await userEvent.click(screen.getByRole("button", { name: "Remove a.zip" }));
    await userEvent.upload(screen.getByTestId("file-input"), [new File(["a"], "a.zip")]);
    await userEvent.click(screen.getByRole("button", { name: "Ingest 2 files" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("limit of 200 MB");
  });
});
