import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import * as f from "./fixtures";
import { location, mockApi, renderApp } from "./utils";

describe("Review queue", () => {
  it("Dependent proposal hint: Recovery Time says its effect waits for the revenue proposal and links to it", async () => {
    const [weight, recovery] = f.queue;
    mockApi({
      "GET /api/stats": f.stats(3),
      "GET /api/proposals": f.queue,
      "GET /api/proposals/2": f.detail(recovery),
      "GET /api/proposals/8": f.detail(weight),
    });
    renderApp("/review/2");
    const queue = await screen.findByRole("list", { name: "Proposals" });
    const items = within(queue).getAllByRole("listitem");
    expect(items.map((i) => i.textContent?.match(/#(\d+)/)?.[1])).toEqual(["8", "2", "3"]); // impact order from the API
    expect(items[0]).toHaveTextContent("up to 88.3 pp exposure");
    expect(items[1]).toHaveTextContent("Its effect appears once proposal #8 (assembly revenue) is accepted.");
    const detail = await screen.findByRole("article", { name: "Proposal 2" });
    const hint = within(detail).getByTestId("dependency-hint");
    await userEvent.click(within(hint).getByRole("link", { name: "proposal #8" }));
    expect(location()).toBe("/data/review/8");
    expect(await screen.findByRole("article", { name: "Proposal 8" })).toBeInTheDocument();
  });

  it("filters by status and kind", async () => {
    const { calls } = mockApi({ "GET /api/stats": f.stats(3), "GET /api/proposals": f.queue, "GET /api/proposals/8": f.detail(f.queue[0]) });
    renderApp("/review");
    await screen.findByRole("list", { name: "Proposals" });
    await userEvent.selectOptions(screen.getByLabelText("Status"), "all");
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "resolution");
    await waitFor(() => {
      const q = new URL(calls.filter((c) => c.path === "/api/proposals").at(-1)!.url, "http://x").searchParams;
      expect([q.get("status"), q.get("kind"), q.get("sort")]).toEqual(["all", "resolution", "impact"]);
    });
  });
});

describe("Proposal detail and decision", () => {
  it("Accept the revenue proposal: facts, replaced values and preview, then the queue and the Overview update", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const [weight, recovery, photo] = f.queue;
    let accepted = false;
    const { calls } = mockApi({
      "GET /api/stats": () => f.stats(accepted ? 2 : 3),
      "GET /api/proposals": () => (accepted ? [{ ...recovery, impact: { ...f.revenueImpact, max_impact_eur: 18_400_000 } }, photo] : f.queue),
      "GET /api/proposals/8": () => (accepted ? { ...f.detail(weight), status: "accepted", decided_by: "ana" } : f.detail(weight, { impact: f.revenueImpact })),
      "GET /api/proposals/2": () => f.detail(recovery, { impact: { ...f.revenueImpact, max_impact_eur: 18_400_000 } }),
      "POST /api/proposals/8/accept": () => {
        accepted = true;
        return f.decision(8, "ana");
      },
      "GET /api/overview": () => (accepted ? f.overviewAccepted : f.overviewPending),
      "GET /api/network": f.network,
    });
    renderApp("/data/review/8");
    const detail = await screen.findByRole("article", { name: "Proposal 8" });
    const facts = within(detail).getByRole("table", { name: "Facts" });
    expect(facts).toHaveTextContent("Assembly DMP3552717");
    expect(facts).toHaveTextContent("990,000,000");
    expect(facts).toHaveTextContent("DMAuto.xlsx Weights!C4");
    expect(within(detail).getByText("Sample of 2 of 8 facts")).toBeInTheDocument();
    expect(within(detail).getByRole("table", { name: "Exposure preview" })).toHaveTextContent("S100228");
    expect(detail).toHaveTextContent("Revenue per assembly in EUR"); // the spec column's rationale

    await userEvent.type(within(detail).getByLabelText("Comment (optional)"), "matches the finance sheet");
    await userEvent.click(within(detail).getByRole("button", { name: "Accept" }));
    await screen.findByText("Proposal #8 accepted");
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ comment: "matches the finance sheet" });
    const queue = screen.getByRole("list", { name: "Proposals" });
    await waitFor(() => expect(within(queue).queryByText(/#8/)).not.toBeInTheDocument());
    expect(within(queue).getAllByRole("listitem")[0]).toHaveTextContent("up to €18.4M"); // Recovery Time now has impact
    expect(within(queue).queryByTestId("dependency-hint")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Overview" }));
    const kpis = await screen.findByRole("region", { name: "Key figures" });
    expect(within(kpis).getByText("Total assembly revenue").nextSibling).toHaveTextContent("€2,400M");
  });

  it("shows all facts on request", async () => {
    const [weight] = f.queue;
    const { calls } = mockApi({
      "GET /api/stats": f.stats(3),
      "GET /api/proposals": f.queue,
      "GET /api/proposals/8": (c: { url: string }) =>
        new URL(c.url, "http://x").searchParams.get("samples") === "8"
          ? f.detail(weight, { facts: Array.from({ length: 8 }, (_, i) => f.fact(`DMP${i}`, i)) })
          : f.detail(weight),
    });
    renderApp("/data/review/8");
    await userEvent.click(await screen.findByRole("button", { name: "Show all 8" }));
    expect(await screen.findByText("All 8 facts")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("samples=8"))).toBe(true);
  });

  it("Resolve a mention: pick a candidate site and the proposal is accepted with it", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const resolution = f.proposal(12, "x", {
      kind: "resolution",
      group_key: ["mention", "letter.pdf", "Stuttgart plant", "Site"],
      summary: "mention 'Stuttgart plant' (Site) in letter.pdf",
    });
    const { calls } = mockApi({
      "GET /api/stats": f.stats(1),
      "GET /api/proposals": [resolution],
      "GET /api/proposals/12": f.detail(resolution, { spec: undefined, mention: "Stuttgart plant", candidates: ["DMA-DE-01", "DMA-DE-02"], targets: ["Site"] }),
      "POST /api/proposals/12/accept": f.decision(12, "ana"),
    });
    renderApp("/review/12");
    const detail = await screen.findByRole("article", { name: "Proposal 12" });
    const accept = within(detail).getByRole("button", { name: "Accept" });
    expect(accept).toBeDisabled(); // a node must be chosen first
    expect(within(detail).getByText(/Which Site does/)).toBeInTheDocument();
    await userEvent.click(within(detail).getByRole("radio", { name: "DMA-DE-01" }));
    await userEvent.click(accept);
    await screen.findByText("Proposal #12 accepted");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ target_id: "DMA-DE-01" });
  });
});
