import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ResolvedRow, ThreadRead } from "@/api/agent";
import type { Report } from "@/api/ops";
import * as f from "./fixtures";
import { location, mockApi, renderApp } from "./utils";

/** Only resolved disruptions are listed: the API answers them alone (a discarded draft stays archived). */
const resolved: ResolvedRow[] = [
  { id: 3, name: "Kessler Antriebe outage", type: "supplier_outage", type_label: "Supplier outage", resolved_at: "2026-10-31T12:00:00+00:00",
    resolved_by: "ana", resolution_note: "Supplier back in production.", thread_id: 8, do_nothing_cost: 4.1e6, plan_cost: 1.2e6, saved: 2.9e6,
    model_cost_usd: 0.0123, plan_version: 2, accepted_actions: 2 },
];

const thread: ThreadRead = {
  thread: { id: 8, disruption_id: 3, title: "Kessler Antriebe outage", status: "archived", needs: null, created_at: "", updated_at: "" },
  messages: [
    { id: 1, thread_id: 8, author: "agent:muse-spark-1.1", role: "agent", blocks: [{ type: "text", text: "I propose two spot buys." }], status: "done", profile: "planner", run_id: null, created_at: "2026-10-31T10:00:00+00:00" },
    { id: 2, thread_id: 8, author: "ana", role: "person", blocks: [{ type: "decision", kind: "disruption", ref: 3, decision: "resolved", comment: "Supplier back in production.", what: "Kessler Antriebe outage" }], status: "done", profile: null, run_id: null, created_at: "2026-10-31T12:00:00+00:00" },
  ],
  runs: [],
  queue: [],
  running: false,
};

const portfolio: Report = {
  kind: "portfolio",
  id: "portfolio",
  title: "Portfolio risk report",
  subtitle: "254 suppliers, 167 bought-in components",
  generated_at: "2026-10-31T10:00:00+00:00",
  sections: [
    { title: "Summary", blocks: [{ kind: "metrics", items: [{ label: "Assembly revenue", value: "EUR 3,400.00M" }, { label: "Single-source", value: "85 (51%)" }] }] },
    {
      title: "Suppliers with the largest Business Impact",
      blocks: [{ kind: "table", columns: ["Supplier", "Country", "Components", "Business Impact"], rows: [["Kessler Antriebe GmbH (S100228)", "Germany", 4, "EUR 18.40M"]] }],
    },
  ],
};

const disruption: Report = {
  kind: "disruption",
  id: 3,
  title: "Disruption report: Kessler Antriebe outage",
  subtitle: "Supplier outage, week 2 for 6 weeks",
  generated_at: "2026-10-31T10:05:00+00:00",
  sections: [
    { title: "Executive summary", note: "Written by muse-spark-1.1", blocks: [{ kind: "text", text: "The outage costs EUR 4.10M if nothing is done." }] },
    {
      title: "Impact if nothing is done",
      blocks: [
        { kind: "metrics", items: [{ label: "Lost revenue", value: "EUR 21.30M" }, { label: "First week with lost output", value: "4" }] },
        { kind: "chart", title: "Lost revenue per week", unit: "EUR", series: [{ name: "Do nothing", values: [0, 0, 0, 0, 2e6, 3e6] }, { name: "Accepted plan", values: [0, 0, 0, 0, 0.5e6, 0] }] },
        { kind: "table", columns: ["Component", "Supplier", "Weeks short"], rows: [["DMC-9948", "Kessler Antriebe GmbH (Germany)", 3], ["DMC-1200", "Kessler Antriebe GmbH (Germany)", null]] },
      ],
    },
    { title: "Agent run", blocks: [{ kind: "list", items: ["simulate_plan: total EUR 1.20M", "submit_plan: 2 actions"] }] },
  ],
};

const benchmark: Report = {
  kind: "benchmark",
  id: "benchmark",
  title: "Agent benchmark",
  subtitle: "2 result sets on the scenario suite",
  generated_at: "2026-11-01T10:00:00+00:00",
  sections: [
    {
      title: "Result sets",
      note: "mean skill over cases with a 95% bootstrap interval",
      blocks: [{ kind: "table", columns: ["Result set", "Model", "Agent skill", "Playbook skill"], rows: [["spark-v3-full-test", "muse-spark-1.1", "0.91 [0.85, 0.96]", "0.82"]] }],
    },
  ],
};

const handlers = (extra: Record<string, unknown> = {}) => ({
  "GET /api/stats": f.stats(0),
  "GET /api/ops/summary": { active: [], open_tasks: 0, in_progress_tasks: 0 },
  "GET /api/ops/resolved": resolved,
  "GET /api/ops/reports/portfolio": portfolio,
  "GET /api/ops/reports/disruption/3": disruption,
  "GET /api/agent/threads/8": thread,
  ...extra,
});

describe("Reports screen", () => {
  it("opens the portfolio report by default and lists the scorecard and the resolved disruptions with their figures", async () => {
    mockApi(handlers());
    renderApp("/reports");
    const doc = await screen.findByRole("article", { name: "Portfolio risk report" });
    expect(doc).toHaveTextContent("254 suppliers, 167 bought-in components");
    const tiles = within(doc).getByText("Assembly revenue").closest("dl")!;
    expect(tiles).toHaveTextContent("EUR 3,400.00M");
    const table = within(doc).getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Supplier", "Country", "Components", "Business Impact"]);
    expect(table).toHaveTextContent("Kessler Antriebe GmbH (S100228)");

    const nav = screen.getByRole("navigation", { name: "Reports" });
    expect(within(nav).getByRole("link", { name: /Portfolio risk report/ })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: /Agent scorecard/ })).toHaveAttribute("href", "/reports/scorecard");
    const entry = await within(nav).findByRole("link", { name: /Kessler Antriebe outage/ });
    expect(entry).toHaveTextContent("€4.1M if nothing was done");
    expect(entry).toHaveTextContent("plan €1.2M");
    expect(entry).toHaveTextContent("€2.9M saved");
    expect(entry).toHaveTextContent("model $0.0123");
    expect(within(within(nav).getByRole("list", { name: "Resolved disruptions" })).getAllByRole("link")).toHaveLength(1);
    await userEvent.click(entry);
    expect(location()).toBe("/reports/d/3");
    expect(await screen.findByRole("article", { name: "Disruption report: Kessler Antriebe outage" })).toBeInTheDocument();
  });

  it("Open a resolved disruption: its thread without a message box or decision controls, next to its report", async () => {
    mockApi(handlers());
    renderApp("/reports/d/3");
    expect(await screen.findByRole("article", { name: "Disruption report: Kessler Antriebe outage" })).toBeInTheDocument();
    const t = screen.getByRole("region", { name: "Thread" });
    expect(await within(t).findByText("I propose two spot buys.")).toBeInTheDocument();
    expect(t).toHaveTextContent("Resolved by ana");
    expect(t).toHaveTextContent("resolved disruption Kessler Antriebe outage");
    expect(screen.queryByRole("textbox", { name: "Message" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Accept|Reject|Mark resolved/ })).not.toBeInTheDocument();
    expect(t.closest("[data-print='hide']")).not.toBeNull(); // the printout is the report alone
  });

  it("Old report address of a disruption opens it among the resolved ones", async () => {
    mockApi(handlers());
    renderApp("/reports/disruption/3");
    expect(location()).toBe("/reports/d/3");
    expect(await screen.findByRole("article", { name: "Disruption report: Kessler Antriebe outage" })).toBeInTheDocument();
  });

  it("renders a disruption report's blocks with the section note, Markdown download and print", async () => {
    mockApi(handlers());
    const print = vi.fn();
    vi.stubGlobal("print", print);
    renderApp("/reports/d/3");
    const doc = await screen.findByRole("article", { name: "Disruption report: Kessler Antriebe outage" });

    const summary = within(doc).getByRole("region", { name: "Executive summary" });
    expect(summary).toHaveTextContent("The outage costs EUR 4.10M if nothing is done.");
    expect(within(summary).getByText("Written by muse-spark-1.1")).toBeInTheDocument();

    const impact = within(doc).getByRole("region", { name: "Impact if nothing is done" });
    expect(within(impact).getByText("Lost revenue").nextSibling).toHaveTextContent("EUR 21.30M");
    expect(within(impact).getByRole("img", { name: "Lost revenue per week" })).toBeInTheDocument();
    expect(impact).toHaveTextContent("Accepted plan");
    const rows = within(within(impact).getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("DMC-9948");
    expect(within(rows[2]).getAllByRole("cell")[2]).toHaveTextContent("—");

    const run = within(doc).getByRole("region", { name: "Agent run" });
    expect(within(run).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["simulate_plan: total EUR 1.20M", "submit_plan: 2 actions"]);

    const download = within(doc).getByRole("link", { name: "Download Markdown" });
    expect(download).toHaveAttribute("href", "/api/ops/reports/disruption/3/markdown");
    expect(download).toHaveAttribute("download");
    await userEvent.click(within(doc).getByRole("button", { name: "Print" }));
    expect(print).toHaveBeenCalledOnce();
  });

  it("marks the list pane and the buttons as screen-only for printing", async () => {
    mockApi(handlers());
    renderApp("/reports/portfolio");
    const doc = await screen.findByRole("article", { name: "Portfolio risk report" });
    expect(screen.getByRole("navigation", { name: "Reports" }).closest("[data-print='hide']")).not.toBeNull();
    expect(within(doc).getByRole("button", { name: "Print" }).closest("[data-print='hide']")).not.toBeNull();
    expect(within(doc).getByRole("link", { name: "Download Markdown" })).toHaveAttribute("href", "/api/ops/reports/portfolio/markdown");
    expect(doc.querySelectorAll(".report-section")).toHaveLength(2);
  });

  it("opens the agent benchmark from the list", async () => {
    mockApi(handlers({ "GET /api/ops/reports/benchmark": benchmark }));
    renderApp("/reports");
    const nav = screen.getByRole("navigation", { name: "Reports" });
    await userEvent.click(within(nav).getByRole("link", { name: /Agent benchmark/ }));
    expect(location()).toBe("/reports/benchmark");
    const doc = await screen.findByRole("article", { name: "Agent benchmark" });
    expect(within(doc).getByRole("region", { name: "Result sets" })).toHaveTextContent("0.91 [0.85, 0.96]");
    expect(within(doc).getByRole("link", { name: "Download Markdown" })).toHaveAttribute("href", "/api/ops/reports/benchmark/markdown");
  });

  it("shows a refused report's reason", async () => {
    mockApi(handlers({ "GET /api/ops/reports/scorecard": new Response(JSON.stringify({ detail: "ops store unavailable" }), { status: 500 }) }));
    renderApp("/reports/scorecard");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("ops store unavailable"));
  });
});
