import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { Message, PlanView, ThreadRead, ThreadRow } from "@/api/agent";
import type { DisruptionDetail, OpsMeta, Outcome } from "@/api/ops";
import { parseStack, serialiseStack } from "@/features/agent/address";
import { DisruptionForm, EMPTY } from "@/features/agent/detail/DisruptionForm";
import { sortThreads } from "@/features/agent/ThreadList";
import * as f from "./fixtures";
import { location, mockApi, renderApp, renderWith, search } from "./utils";

const T = "2026-10-31T10:00:00+00:00";

const outcome = (total: number, weekly: number[] = [0, 0, 0, 1e6]): Outcome => ({
  total_cost: total, lost_margin: total, lost_revenue: total * 5, action_cost: total / 10, action_costs: { "spot_buy:DMC-9948": 1200, "spot_buy:DMC-1200": 800 },
  disruption_costs: 0, weekly_lost_revenue: weekly, lost_revenue_by_assembly: {}, assembly_output: {}, first_stop_week: 3, short_components: {}, refused: [],
});

const planView = (version: number, current: boolean, extra: Partial<PlanView> = {}): PlanView => ({
  id: version, disruption_id: 1, version, run_id: version, graph_version: 1, summary: `Version ${version}.`, skill: 0.9, created_at: T,
  actions: [
    { action: { type: "spot_buy", component: "DMC-9948", share: 0.5 }, text: "Spot buy 50% of DMC-9948", reason: "bridge the gap", kept: false, action_id: 21, status: "proposed", comment: null },
    { action: { type: "spot_buy", component: "DMC-1200", share: 0.3 }, text: "Spot buy 30% of DMC-1200", reason: "second part", kept: false, action_id: 22, status: "proposed", comment: null },
    { action: { type: "priority", assemblies: ["A1"] }, text: "Give A1 priority", reason: "most revenue", kept: false, action_id: 23, status: "proposed", comment: null },
  ],
  outcome: outcome(1.2e6), stale: null, current, do_nothing_cost: 33.9e6, spend_limit_eur: 250000, over_limit: false, ...extra,
});

const msg = (id: number, role: Message["role"], blocks: Message["blocks"], extra: Partial<Message> = {}): Message => ({
  id, thread_id: 1, author: role === "agent" ? "agent:muse-spark-1.1" : role === "system" ? "system" : "ana", role, blocks, status: "done",
  profile: null, run_id: null, created_at: T, ...extra,
});

const tool = (name: string, input: Record<string, unknown>, result: unknown) => ({ kind: "tool" as const, t: 1, name, input, result });

function threadData(extra: Partial<ThreadRead> = {}, messages: Message[] = []): ThreadRead {
  return {
    thread: { id: 1, disruption_id: 1, title: "Fire at Kessler Antriebe", status: "active", needs: "plan ready", created_at: T, updated_at: T },
    messages: [
      msg(1, "system", [{ type: "event", ref: 1, event: { id: 1, type: "disruption.created", payload: {}, source: "library", thread_id: 1, run_id: null, created_at: T } }]),
      msg(2, "agent", [{ type: "plan", ref: 1, plan: planView(1, false) }], { run_id: 1 }),
      msg(3, "person", [{ type: "text", text: "Avoid air freight." }]),
      msg(4, "agent", [{ type: "text", text: "Kessler S100228 is the only source of DMC-9948.\n\n- spot buy two parts\n- give A1 priority" }, { type: "plan", ref: 2, plan: planView(2, true) }], { run_id: 2 }),
      ...messages,
    ],
    runs: [
      { id: 1, status: "done", profile: "planner", error: null, steps: [tool("simulate_plan", { actions: [] }, { total_cost: "EUR 2.00M" })] },
      { id: 2, status: "done", profile: "planner", error: null, steps: [
        tool("component_details", { component: "DMC-9948" }, { component: "DMC-9948", supplier: "S100228" }),
        tool("alternative_suppliers", { component: "DMC-9948" }, { suppliers: [] }),
        tool("simulate_plan", { actions: [{}, {}] }, { total_cost: "EUR 1.50M", change_vs_doing_nothing: "-EUR 32.40M" }),
        tool("simulate_plan", { actions: [{}, {}, {}] }, { total_cost: "EUR 1.20M", change_vs_doing_nothing: "-EUR 32.70M" }),
        tool("submit_plan", { actions: [] }, { accepted: true, total_cost: "EUR 1.20M" }),
      ] },
    ],
    queue: [],
    running: false,
    ...extra,
  };
}

function detailOf(status: "draft" | "active" = "active", plan: PlanView | null = planView(2, true)): DisruptionDetail {
  return {
    disruption: { id: 1, status, type_label: "Supplier outage", created_at: T, author: "ana", name: "Fire at Kessler Antriebe", type: "supplier_outage",
      start_week: 0, duration_weeks: 6, description: "A fire stops the plant.", params: { suppliers: ["S100228"] }, stock_cover_weeks: 3, horizon_weeks: 26,
      source: "library", warnings: [] },
    assessment: { affected_components: [], affected_suppliers: [], affected_sites: [], assemblies: [], do_nothing: outcome(33.9e6), margin: 0.19, margin_from_graph: true },
    references: { do_nothing: { plan: [], outcome: outcome(33.9e6) }, playbook: { plan: [], outcome: outcome(5e6), skill: 0.8 }, optimiser: { plan: [], outcome: outcome(1e6), skill: 1 } },
    runs: [], latest_run: null, actions: [], accepted: { plan: [], outcome: null, skill: null },
    tasks: [{ id: 11, action_id: 20, disruption_id: 1, title: "Spot buy 50% of DMC-9948", status: "in_progress", owner: "bo", due_week: 2, notes: "", updated_by: null, created_at: T, updated_at: T }],
    rules: {}, plan,
  };
}

const row = (id: number, title: string, extra: Partial<ThreadRow> = {}): ThreadRow => ({
  id, disruption_id: id, disruption_status: "active", title, status: "active", needs: null, created_at: T, updated_at: `2026-10-31T0${id}:00:00+00:00`,
  last: { author: "agent:x", role: "agent", created_at: `2026-10-31T0${id}:00:00+00:00`, text: "…" }, ...extra,
});

const threads: ThreadRow[] = [row(1, "Fire at Kessler Antriebe", { needs: "plan ready" }), row(2, "Port strike"), { ...row(5, "Which parts are single-source?"), disruption_id: null, disruption_status: null }];

function api(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/stats": f.stats(0),
    "GET /api/ops/summary": { ...f.opsSummary(), agent_needs: 1, tasks: [{ id: 11, disruption_id: 1, disruption_name: "Fire at Kessler Antriebe", disruption_status: "active",
      thread_id: 1, title: "Spot buy 50% of DMC-9948", status: "in_progress", owner: "bo", due_week: 2, overdue: true, elapsed_week: 3 }] },
    "GET /api/ops/meta": f.opsMeta,
    "GET /api/ops/library": [],
    "GET /api/agent/threads": threads,
    "GET /api/agent/threads/1": threadData(),
    "GET /api/ops/disruptions/1": detailOf(),
    "GET /api/network": f.network,
    "GET /api/schema": f.schema,
    "GET /api/nodes/Supplier/S100228": f.supplierNode,
    "GET /api/exposure/Supplier/S100228": f.supplierExposure,
    ...extra,
  };
}

const detailColumn = () => screen.findByRole("complementary", { name: "Detail" });
const threadPane = () => screen.findByRole("region", { name: "Thread" });

beforeEach(() => localStorage.setItem("tierscope.author", "Ana"));

describe("Workspace layout", () => {
  it("Deep link to a plan: the thread is shown with plan version 2 open in the detail column", async () => {
    mockApi(api());
    renderApp("/agent/t/1?d=plan:2");
    expect(await within(await threadPane()).findByText(/is the only source of/)).toBeInTheDocument();
    const detail = await detailColumn();
    expect(await within(detail).findByRole("heading", { name: "Plan v2" })).toBeInTheDocument();
    expect(within(within(detail).getByRole("navigation", { name: "Breadcrumb" })).getByText("Plan v2")).toHaveAttribute("aria-current", "page");
  });

  it("keeps the stack in the address and returns to the incident state with Escape", async () => {
    mockApi(api());
    renderApp("/agent/t/1");
    expect(await within(await detailColumn()).findByRole("region", { name: "Impact" })).toBeInTheDocument();
    await userEvent.click(within(await threadPane()).getByRole("button", { name: /Plan v2/ }));
    expect(location()).toBe("/agent/t/1");
    expect(search()).toBe("?d=plan:2");
    await userEvent.keyboard("{Escape}");
    expect(search()).toBe("");
    expect(within(await detailColumn()).getByRole("region", { name: "Impact" })).toBeInTheDocument();
  });

  it("parses and writes the stack, with ids holding colons and commas", () => {
    const stack = parseStack("?d=plan:2,node:Supplier:S1%2CX,report");
    expect(stack).toEqual([{ kind: "plan", id: "2" }, { kind: "node", id: "Supplier:S1,X" }, { kind: "report", id: "" }]);
    expect(serialiseStack(stack)).toBe("?d=plan:2,node:Supplier:S1%2CX,report");
    expect(parseStack("?d=bogus:1,node:Supplier")).toEqual([]);
  });
});

describe("Disruptions and conversations list", () => {
  it("Plan ready moves up: the disruption listed third moves to the top with the mark", async () => {
    let rows = [row(3, "Typhoon"), row(2, "Port strike"), row(1, "Fire at Kessler Antriebe")];
    mockApi(api({ "GET /api/agent/threads": () => rows }));
    const { client } = renderApp("/agent");
    const section = await screen.findByRole("region", { name: "Disruptions" });
    await waitFor(() => expect(within(section).getAllByRole("link").map((a) => a.textContent)).toHaveLength(3));
    expect(within(section).getAllByRole("link")[2]).toHaveTextContent("Fire at Kessler Antriebe");
    rows = [row(3, "Typhoon"), row(2, "Port strike"), row(1, "Fire at Kessler Antriebe", { needs: "plan ready" })];
    await client.invalidateQueries({ queryKey: ["agent"] });
    await waitFor(() => expect(within(section).getAllByRole("link")[0]).toHaveTextContent("Fire at Kessler Antriebe"));
    expect(within(section).getAllByRole("link")[0]).toHaveTextContent("plan ready");
  });

  it("Overdue task marks its disruption; drafts are labelled; conversations sit apart, older ones folded", async () => {
    const old = { ...row(6, "Old question"), disruption_id: null, disruption_status: null, updated_at: "2026-01-01T00:00:00+00:00", last: null } as ThreadRow;
    mockApi(api({ "GET /api/agent/threads": [row(1, "Fire at Kessler Antriebe", { needs: "task overdue" }), row(2, "Port strike", { disruption_status: "draft" }), threads[2], old] }));
    renderApp("/agent");
    const disruptions = await screen.findByRole("region", { name: "Disruptions" });
    const fire = await within(disruptions).findByRole("link", { name: /Fire at Kessler Antriebe/ });
    expect(within(fire).getByText("task overdue")).toBeInTheDocument();
    expect(within(within(disruptions).getByRole("link", { name: /Port strike/ })).getByText("draft")).toBeInTheDocument();
    const conversations = screen.getByRole("region", { name: "Conversations" });
    expect(within(conversations).getAllByRole("link")).toHaveLength(1);
    await userEvent.click(within(conversations).getByRole("button", { name: "Older (1)" }));
    expect(within(conversations).getByRole("link", { name: /Old question/ })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("textbox", { name: "Search threads" }), "port");
    expect(within(disruptions).getAllByRole("link")).toHaveLength(1);
  });

  it("sorts disruptions needing a person first, then by last activity", () => {
    const { disruptions } = sortThreads([row(1, "a"), row(2, "b", { needs: "budget" }), row(3, "c")]);
    expect(disruptions.map((t) => t.title)).toEqual(["b", "c", "a"]);
  });
});

describe("Thread view and cards", () => {
  it("Steps folded by default: an answer after five tool calls shows '5 steps' that expands to the five calls", async () => {
    mockApi(api());
    renderApp("/agent/t/1");
    const answer = (await within(await threadPane()).findAllByRole("listitem", { name: /Message from Agent/ }))[1];
    const fold = within(answer).getByRole("button", { name: "5 steps" });
    expect(fold).toHaveAttribute("aria-expanded", "false");
    expect(within(answer).queryByRole("list", { name: "Agent steps" })).not.toBeInTheDocument();
    await userEvent.click(fold);
    const steps = within(answer).getByRole("list", { name: "Agent steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(5);
    expect(steps).toHaveTextContent("Looked up DMC-9948");
    expect(steps).toHaveTextContent('"supplier": "S100228"'); // each call's result
  });

  it("Plan card: one line 'Plan v2' with the do-nothing and plan costs and '3 actions'; v1 above reads superseded", async () => {
    mockApi(api());
    renderApp("/agent/t/1");
    const cards = await within(await threadPane()).findAllByRole("button", { name: /Plan v\d/ });
    expect(cards.map((c) => c.textContent?.match(/Plan v\d/)?.[0])).toEqual(["Plan v1", "Plan v2"]);
    expect(cards[0]).toHaveTextContent("superseded");
    expect(cards[1]).toHaveTextContent("€33.9M if nothing is done → €1.2M");
    expect(cards[1]).toHaveTextContent("3 actions");
    expect(cards[1]).toHaveTextContent("0 decided");
    expect(cards[1]).not.toHaveTextContent("superseded");
  });

  it("keeps rendering as steps arrive where scrolling returns a promise (recent browsers)", async () => {
    const scroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () {
      return Promise.resolve() as unknown as void;
    };
    try {
      let data = threadData({ running: true, runs: [{ id: 3, status: "running", profile: "chat", error: null, steps: [] }] });
      mockApi(api({ "GET /api/agent/threads/1": () => data }));
      const { client } = renderApp("/agent/t/1");
      await within(await threadPane()).findByText("0 steps so far", { exact: false });
      data = threadData({ running: true, runs: [{ id: 3, status: "running", profile: "chat", error: null, steps: [tool("component_details", { component: "DMC-1200" }, {})] }] });
      await client.invalidateQueries({ queryKey: ["agent"] });
      expect(await within(await threadPane()).findByText("Looked up DMC-1200")).toBeInTheDocument();
    } finally {
      Element.prototype.scrollIntoView = scroll;
    }
  });

  it("marks queued messages and shows a running turn's steps as they happen", async () => {
    const running = threadData({ running: true, queue: [9], runs: [...threadData().runs, { id: 3, status: "running", profile: "chat", error: null,
      steps: [tool("component_details", { component: "DMC-1200" }, {})] }] }, [msg(9, "person", [{ type: "text", text: "And DMC-1200?" }], { status: "queued", profile: "chat" })]);
    mockApi(api({ "GET /api/agent/threads/1": running }));
    renderApp("/agent/t/1");
    const queued = (await within(await threadPane()).findAllByRole("listitem", { name: "Message from ana" })).at(-1)!;
    expect(queued).toHaveTextContent("And DMC-1200?");
    expect(within(queued).getByText("queued")).toBeInTheDocument();
    expect(within(await threadPane()).getAllByText("queued")).toHaveLength(1);
    const working = within(await threadPane()).getByRole("listitem", { name: "Working" });
    expect(working).toHaveTextContent("1 step so far");
    expect(working).toHaveTextContent("Looked up DMC-1200");
    expect(screen.getByRole("textbox", { name: "Message" })).toHaveAttribute("placeholder", expect.stringContaining("waits its turn"));
  });
});

describe("Detail column", () => {
  it("Node from an answer: the supplier id opens its node panel and the breadcrumb reads the incident and the supplier", async () => {
    mockApi(api());
    renderApp("/agent/t/1");
    await userEvent.click(await within(await threadPane()).findByRole("button", { name: "S100228" }));
    expect(search()).toBe("?d=node:Supplier:S100228");
    const detail = await detailColumn();
    expect(await within(detail).findByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
    const crumbs = within(detail).getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Incident", "Kessler Antriebe GmbH"]);
    // only ids the tools returned are links
    expect(within(await threadPane()).queryByRole("button", { name: "Kessler" })).not.toBeInTheDocument();
  });

  it("Agent answers while a node is open: the column keeps the supplier and the new plan card appears in the thread", async () => {
    let data = threadData();
    mockApi(api({ "GET /api/agent/threads/1": () => data }));
    const { client } = renderApp("/agent/t/1?d=node:Supplier:S100228");
    expect(await within(await detailColumn()).findByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
    data = threadData({}, [msg(5, "agent", [{ type: "plan", ref: 3, plan: planView(3, true) }], { run_id: 2 })]);
    await client.invalidateQueries({ queryKey: ["agent"] });
    expect(await within(await threadPane()).findByRole("button", { name: /Plan v3/ })).toBeInTheDocument();
    expect(search()).toBe("?d=node:Supplier:S100228");
    expect(within(await detailColumn()).getByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
  });

  it("Reject with a comment: the action shows as rejected with the comment and the thread shows the decision line", async () => {
    let data = threadData();
    let detail = detailOf();
    const { calls } = mockApi(api({
      "GET /api/agent/threads/1": () => data,
      "GET /api/ops/disruptions/1": () => detail,
      "POST /api/ops/actions/21/reject": () => {
        const rejected = planView(2, true);
        rejected.actions[0] = { ...rejected.actions[0], status: "rejected", comment: "no suppliers in China" };
        data = threadData({}, [msg(5, "person", [{ type: "decision", kind: "action", ref: 21, decision: "rejected", comment: "no suppliers in China", what: "Spot buy 50% of DMC-9948" }], { author: "Ana" })]);
        data.messages[3].blocks[1] = { type: "plan", ref: 2, plan: rejected };
        detail = { ...detailOf(), plan: rejected };
        return { id: 21, status: "rejected" };
      },
    }));
    renderApp("/agent/t/1?d=plan:2");
    const plan = await within(await detailColumn()).findByLabelText("Plan v2");
    expect(within(plan).getByRole("region", { name: "DMC-9948" })).toBeInTheDocument();
    expect(within(plan).getByRole("region", { name: "All assemblies" })).toBeInTheDocument();
    const action = within(plan).getByLabelText("Spot buy 50% of DMC-9948");
    await userEvent.type(within(action).getByRole("textbox"), "no suppliers in China");
    await userEvent.click(within(action).getByRole("button", { name: /Reject/ }));
    const sent = calls.find((c) => c.path === "/api/ops/actions/21/reject")!;
    expect(sent.body).toEqual({ comment: "no suppliers in China" });
    expect(sent.headers["X-Author"]).toBe("Ana");
    await waitFor(async () => expect(within(within(await detailColumn()).getByLabelText("Spot buy 50% of DMC-9948")).getByText("rejected")).toBeInTheDocument());
    expect(within(await detailColumn()).getByLabelText("Spot buy 50% of DMC-9948")).toHaveTextContent("“no suppliers in China”");
    expect(await within(await threadPane()).findByText(/rejected action Spot buy 50% of DMC-9948/)).toBeInTheDocument();
  });

  it("Apply an edit: the block shows applied with the user as author and the thread shows the decision line", async () => {
    const edit = (status: "proposed" | "applied") => ({
      id: 5, thread_id: 1, edits: [], before: [], preview: { changed: 3 }, reason: "the supplier said so", status, decided_by: status === "applied" ? "Ana" : null,
      decided_at: status === "applied" ? T : null, journal_id: status === "applied" ? 40 : null, created_at: T,
      changes: [{ edit: "set Site DMA-DE-01 recovery_time_months", old: 6, new: 9 }],
    });
    let data = threadData({}, [msg(5, "agent", [{ type: "text", text: "Proposed." }, { type: "edit", ref: 5, edit: edit("proposed") }])]);
    const { calls } = mockApi(api({
      "GET /api/agent/threads/1": () => data,
      "POST /api/agent/edits/5/apply": () => {
        data = threadData({}, [msg(5, "agent", [{ type: "text", text: "Proposed." }, { type: "edit", ref: 5, edit: edit("applied") }]),
          msg(6, "person", [{ type: "decision", kind: "edit", ref: 5, decision: "applied", what: "journal #40" }], { author: "Ana" })]);
        return { journal_id: 40 };
      },
    }));
    renderApp("/agent/t/1");
    const card = await within(await threadPane()).findByRole("button", { name: /Edit.*recovery_time_months/ });
    expect(card).toHaveTextContent("(6 → 9)");
    await userEvent.click(card);
    const panel = await within(await detailColumn()).findByLabelText("Edit 5");
    expect(within(panel).getByRole("table", { name: "Changes" })).toHaveTextContent("set Site DMA-DE-01 recovery_time_months69");
    await userEvent.click(within(panel).getByRole("button", { name: /Apply/ }));
    expect(calls.find((c) => c.path === "/api/agent/edits/5/apply")?.headers["X-Author"]).toBe("Ana");
    await waitFor(async () => expect(within(await detailColumn()).getByLabelText("Edit 5")).toHaveTextContent("appliedby Ana"));
    expect(await within(await threadPane()).findByText(/applied edit journal #40/)).toBeInTheDocument();
  });

  it("opens a task from the incident state and saves its changes", async () => {
    const { calls } = mockApi(api({ "POST /api/ops/tasks/11": { id: 11 } }));
    renderApp("/agent/t/1");
    await userEvent.click(await within(await detailColumn()).findByRole("button", { name: /Spot buy 50% of DMC-9948/ }));
    const task = within(await detailColumn()).getByLabelText("Task Spot buy 50% of DMC-9948");
    expect(task).toHaveTextContent("overdue");
    await userEvent.selectOptions(within(task).getByLabelText("Status"), "done");
    await userEvent.type(within(task).getByLabelText("Notes"), "ordered");
    await userEvent.click(within(task).getByRole("button", { name: "Save task" }));
    expect(calls.find((c) => c.path === "/api/ops/tasks/11")?.body).toEqual({ status: "done", owner: "bo", due_week: 2, notes: "ordered" });
  });
});

describe("Message box and quick actions", () => {
  it("Quick actions for a draft with a plan: Confirm and Discard, not Plan a response", async () => {
    const { calls } = mockApi(api({ "GET /api/ops/disruptions/1": detailOf("draft"), "POST /api/ops/disruptions/1/confirm": { id: 1, status: "active" } }));
    renderApp("/agent/t/1");
    const quick = await screen.findByLabelText("Quick actions");
    expect(within(quick).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual(["Confirm", "Discard"]);
    await userEvent.click(within(quick).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/ops/disruptions/1/confirm")).toBe(true));
  });

  it("offers Plan a response without a plan", async () => {
    mockApi(api({ "GET /api/ops/disruptions/1": detailOf("active", null) }));
    renderApp("/agent/t/1");
    const quick = await screen.findByLabelText("Quick actions");
    expect(within(quick).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual(["Plan a response", "Mark resolved"]);
  });

  it("offers Ask for a new version once an action of the plan is rejected", async () => {
    const rejected = planView(2, true);
    rejected.actions[0].status = "rejected";
    const { calls } = mockApi(api({ "GET /api/ops/disruptions/1": { ...detailOf(), plan: rejected }, "POST /api/ops/disruptions/1/runs": { run_id: 3, job_id: "j" } }));
    renderApp("/agent/t/1");
    const quick = await screen.findByLabelText("Quick actions");
    expect(within(quick).getAllByRole("button").map((b) => b.textContent?.trim())).toEqual(["Ask for a new version", "Mark resolved"]);
    await userEvent.click(within(quick).getByRole("button", { name: /Ask for a new version/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/ops/disruptions/1/runs")).toBe(true));
  });

  it("sends a message to the thread with the author", async () => {
    const { calls } = mockApi(api({ "POST /api/agent/threads/1/messages": { message_id: 10 } }));
    renderApp("/agent/t/1");
    await userEvent.type(await screen.findByRole("textbox", { name: "Message" }), "What about DMC-1200?{Enter}");
    const sent = calls.find((c) => c.path === "/api/agent/threads/1/messages")!;
    expect(sent.body).toEqual({ text: "What about DMC-1200?" });
    expect(sent.headers["X-Author"]).toBe("Ana");
  });
});

describe("Starting a conversation", () => {
  const suez = { name: "Suez Canal closure", type: "route_closure", type_label: "Route closure", start_week: 0, duration_weeks: 6, description: "",
    params: {}, stock_cover_weeks: 3, horizon_weeks: 26, source: "library", affected_components: 40, do_nothing_cost: 12.5e6, lost_revenue: 6e7 };

  it("Start from the library: the disruption is created and its thread opens in the Disruptions section", async () => {
    const created = threadData({ thread: { ...threadData().thread, id: 4, disruption_id: 9, title: "Suez Canal closure", needs: null }, running: true,
      runs: [{ id: 7, status: "running", profile: "planner", error: null, steps: [tool("component_details", { component: "DMC-1" }, {})] }] }, []);
    created.messages = [];
    let rows = threads;
    const { calls } = mockApi(api({
      "GET /api/ops/library": [suez],
      "POST /api/ops/disruptions": () => ((rows = [row(4, "Suez Canal closure"), ...threads]), { id: 9 }),
      "GET /api/agent/threads": (c: { url: string }) => (c.url.includes("disruption=9") ? [row(4, "Suez Canal closure")] : rows),
      "GET /api/agent/threads/4": created,
      "GET /api/ops/disruptions/9": { ...detailOf(), plan: null },
    }));
    renderApp("/agent");
    expect(await screen.findByText("€12.5M")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start Suez Canal closure" }));
    await waitFor(() => expect(location()).toBe("/agent/t/4"));
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/ops/disruptions")?.body).toMatchObject({ name: "Suez Canal closure" });
    const section = screen.getByRole("region", { name: "Disruptions" });
    expect(await within(section).findByRole("link", { name: /Suez Canal closure/ })).toHaveAttribute("aria-current", "page");
    expect(await within(await threadPane()).findByText("Looked up DMC-1")).toBeInTheDocument(); // the planner's steps
  });

  it("a first question opens a general thread", async () => {
    const { calls } = mockApi(api({ "POST /api/agent/threads": { thread_id: 5, message_id: 1 }, "GET /api/agent/threads/5": threadData({ thread: { ...threadData().thread, id: 5, disruption_id: null } }, []) }));
    renderApp("/agent");
    await userEvent.click(await screen.findByRole("button", { name: /Which single-source parts/ }));
    await waitFor(() => expect(location()).toBe("/agent/t/5"));
    expect(calls.find((c) => c.path === "/api/agent/threads" && c.method === "POST")?.body).toEqual({ text: "Which single-source parts carry the most revenue?" });
  });
});

describe("Resolving and the model", () => {
  it("Resolve with an open task: the confirmation names it, and after confirming Reports opens", async () => {
    const { calls } = mockApi(api({
      "POST /api/ops/disruptions/1/resolve": { id: 1, status: "resolved", thread_id: 1 },
      "GET /api/ops/resolved": [], "GET /api/ops/reports/disruption/1": { kind: "disruption", id: 1, title: "Disruption report: Fire", subtitle: "", generated_at: T, sections: [] },
    }));
    renderApp("/agent/t/1");
    await userEvent.click(await screen.findByRole("button", { name: /Mark resolved/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("list", { name: "Open tasks" })).toHaveTextContent("Spot buy 50% of DMC-9948 (in progress, bo)");
    expect(dialog).toHaveTextContent("1 task is still open and will stay open");
    await userEvent.type(within(dialog).getByLabelText("Closing note"), "Plant restarted.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(location()).toBe("/reports/d/1"));
    expect(calls.find((c) => c.path === "/api/ops/disruptions/1/resolve")?.body).toEqual({ note: "Plant restarted." });
    expect(calls.some((c) => c.path.startsWith("/api/ops/tasks"))).toBe(false); // the task is left as it is
  });

  it("No model configured: the header says so and the message box and planning are disabled", async () => {
    mockApi(api({ "GET /api/ops/meta": { ...f.opsMeta, model: null }, "GET /api/ops/disruptions/1": detailOf("active", null) }));
    renderApp("/agent/t/1");
    await waitFor(async () => expect(await screen.findByLabelText("Model")).toHaveTextContent("No model is configured"));
    const box = screen.getByRole("textbox", { name: "Message" });
    await waitFor(() => expect(box).toBeDisabled());
    expect(box).toHaveAttribute("placeholder", expect.stringContaining("No model is configured"));
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: /Plan a response/ })).toBeDisabled();
  });

  it("shows the configured model and provider in the header", async () => {
    mockApi(api());
    renderApp("/agent");
    await waitFor(async () => expect(await screen.findByLabelText("Model")).toHaveTextContent("muse-spark-1.1 via meta"));
  });
});

describe("Discuss a node", () => {
  it("Discuss a supplier: Agent opens a new conversation about it with its node panel in the detail column", async () => {
    const general = threadData({ thread: { ...threadData().thread, id: 9, disruption_id: null, title: "About Supplier S100228", needs: null } }, []);
    general.messages = [msg(1, "person", [{ type: "text", text: "About Supplier S100228 (Kessler Antriebe GmbH)." }], { thread_id: 9, status: "queued" })];
    const { calls } = mockApi(api({ ...f.overviewApi(), "GET /api/ops/summary": f.opsSummary(), "GET /api/ops/meta": f.opsMeta, "GET /api/schema": f.schema,
      "POST /api/agent/threads": { thread_id: 9, message_id: 1 }, "GET /api/agent/threads/9": general }));
    renderApp("/?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await userEvent.click(await within(panel).findByRole("button", { name: /Discuss/ }));
    await waitFor(() => expect(location()).toBe("/agent/t/9"));
    expect(search()).toBe("?d=node:Supplier:S100228");
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/agent/threads")?.body).toEqual({ text: "About Supplier S100228 (Kessler Antriebe GmbH)." });
    expect(await within(await detailColumn()).findByRole("heading", { name: "Kessler Antriebe GmbH" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toBeEnabled();
  });
});

describe("Open tasks on the Overview", () => {
  it("Open task on the Overview: clicking it opens Agent on its disruption's thread with the task in the detail column", async () => {
    const summary = api()["GET /api/ops/summary"];
    mockApi({ ...f.overviewApi(), ...api(), "GET /api/overview": f.overviewAccepted, "GET /api/ops/summary": summary });
    renderApp("/");
    const tasks = await screen.findByLabelText("Open tasks");
    const link = await within(tasks).findByRole("link", { name: /Spot buy 50% of DMC-9948, Fire at Kessler Antriebe/ });
    expect(link).toHaveTextContent("bo · week 2");
    expect(within(link).getByText("overdue")).toBeInTheDocument();
    await userEvent.click(link);
    expect(location()).toBe("/agent/t/1");
    expect(search()).toBe("?d=task:11");
    expect(await within(await detailColumn()).findByLabelText("Task Spot buy 50% of DMC-9948")).toBeInTheDocument();
  });
});

describe("Earlier addresses", () => {
  it("an active disruption's old address opens its thread; the list and the board open Agent", async () => {
    mockApi(api({ "GET /api/agent/threads": (c: { url: string }) => (c.url.includes("disruption=1") ? [threads[0]] : threads) }));
    renderApp("/disruptions/1");
    await waitFor(() => expect(location()).toBe("/agent/t/1"));
    mockApi(api());
    renderApp("/actions");
    expect(location()).toBe("/agent");
    renderApp("/disruptions");
    expect(location()).toBe("/agent");
  });

  it("Old disruption link: a resolved disruption 4 opens in Reports", async () => {
    mockApi(api({ "GET /api/agent/threads": [{ ...row(4, "Typhoon"), status: "archived", disruption_status: "resolved" }], "GET /api/ops/resolved": [],
      "GET /api/ops/reports/disruption/4": { kind: "disruption", id: 4, title: "Disruption report: Typhoon", subtitle: "", generated_at: T, sections: [] } }));
    renderApp("/disruptions/4");
    await waitFor(() => expect(location()).toBe("/reports/d/4"));
  });
});

describe("Disruption form", () => {
  it("aligns the inputs of a row to the top, whether or not a field has a hint", () => {
    renderWith(<DisruptionForm meta={f.opsMeta as unknown as OpsMeta} initial={EMPTY} onSubmit={() => {}} submitLabel="Save" />);
    const week = ["Starts in week", "Lasts (weeks)", "Stock cover (weeks)", "Horizon (weeks)"].map((l) => screen.getByLabelText(l).parentElement!);
    expect(new Set(week.map((w) => w.className)).size).toBe(1);
    for (const w of week) expect(w).toHaveClass("content-start");
    expect(screen.getByLabelText("Suppliers").closest("div")).toHaveClass("min-h-9", "py-0.5");
  });
});
