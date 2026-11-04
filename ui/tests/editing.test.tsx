import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import * as f from "./fixtures";
import { jsonResponse, mockApi, renderApp, search } from "./utils";

const edited = f.fieldValue(9, {
  method: "human",
  locked: true,
  source: { file: "edit", note: "buyer call 29.10", author: "ana" },
  label: "edit by ana",
  author: "ana",
});

describe("Editing in the interface", () => {
  it("Change a value with evidence: the panel shows the new human, locked value with the comment and exposure updates", async () => {
    localStorage.setItem("tierscope.author", "ana");
    let saved = false;
    const { calls } = mockApi({
      ...f.overviewApi(),
      "GET /api/stats": f.stats(9),
      "GET /api/nodes/Site/DMA-DE-01": () => f.siteNode(saved ? [edited, f.fieldValue(6, { active: false, review: "superseded" })] : [f.fieldValue(6)]),
      "GET /api/exposure/Site/DMA-DE-01": () => ({ ...f.supplierExposure, type: "Site", id: "DMA-DE-01", business_exposure_pct: saved ? 61 : 55 }),
      "POST /api/edits": () => {
        saved = true;
        return { journal: { id: 3, ts: "2026-10-29T20:00:00+00:00", actor: "ana", action: "edit", summary: "set", items: 1, undone_by: null }, edits: [], proposals_created: [] };
      },
    });
    renderApp("/?sel=Site:DMA-DE-01");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    expect(await within(panel).findByTestId("exposure")).toHaveTextContent("55.00%");
    await userEvent.click(within(panel).getByRole("button", { name: "Change Recovery time months" }));
    const dialog = await screen.findByRole("dialog", { name: "Change a value" });
    const value = within(dialog).getByLabelText("New value");
    await userEvent.clear(value);
    await userEvent.type(value, "9");
    await userEvent.type(within(dialog).getByLabelText("Comment"), "buyer call 29.10");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({
      edits: [{ op: "set_field", type: "Site", key: ["DMA-DE-01"], field: "recovery_time_months", value: 9 }],
      comment: "buyer call 29.10",
    });
    expect(post.headers["X-Author"]).toBe("ana");
    await waitFor(() => expect(within(panel).getByTestId("field-recovery_time_months")).toHaveTextContent("9"));
    expect(within(panel).getByTestId("exposure")).toHaveTextContent("61.00%");
    const networkReads = calls.filter((c) => c.path === "/api/network");
    expect(networkReads.length).toBeGreaterThan(1); // the network on the Overview refreshes too
    expect(calls.indexOf(networkReads.at(-1)!)).toBeGreaterThan(calls.indexOf(post));
    await userEvent.click(within(panel).getByRole("button", { name: "Show sources of Recovery time months" }));
    const [active] = within(within(panel).getByRole("list", { name: "Candidates of Recovery time months" })).getAllByRole("listitem");
    expect(active).toHaveTextContent("locked");
    expect(active).toHaveTextContent("person");
    expect(active).toHaveTextContent("buyer call 29.10");
  });

  it("Invalid edit: the API's message is shown at the edit and nothing else changes", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const { calls } = mockApi({
      "GET /api/stats": f.stats(9),
      "GET /api/schema": f.schema,
      "GET /api/nodes/Supplier/S100228": f.supplierNode,
      "GET /api/exposure/Supplier/S100228": f.supplierExposure,
      "GET /api/search": [{ type: "Component", id: "DMC-0000", name: "Nothing", score: 0.6 }],
      "POST /api/edits": () =>
        jsonResponse(422, { detail: { message: "no edit applied", errors: [{ index: 0, message: "no Component 'DMC-0000' in the graph" }] } }),
    });
    renderApp("/?sel=Supplier:S100228");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Kessler Antriebe GmbH" });
    const reads = calls.length;
    await userEvent.click(within(panel).getByRole("button", { name: "Edge" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an edge" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Relation"), "SUPPLIES");
    await userEvent.type(within(dialog).getByLabelText(/To Component/), "DMC-0000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("no edit applied");
    expect(alert).toHaveTextContent("no Component 'DMC-0000' in the graph");
    expect(screen.getByRole("dialog", { name: "Add an edge" })).toBeInTheDocument();
    const after = calls.slice(reads).filter((c) => c.method === "GET" && c.path !== "/api/search");
    expect(after).toEqual([]); // nothing refetched: the rest of the screen stays as it was
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ edits: [{ op: "add_edge", type: "SUPPLIES", key: ["S100228", "DMC-0000", null] }] });
  });

  it("creates a node, deletes with a reason and merges, each as one edit", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const { calls } = mockApi({
      "GET /api/stats": f.stats(9),
      "GET /api/schema": f.schema,
      "GET /api/search": [{ type: "Supplier", id: "S523036", name: "Orbis Plastics", score: 0.9 }],
      "GET /api/nodes/Supplier/S100228": f.supplierNode,
      "GET /api/exposure/Supplier/S100228": f.supplierExposure,
      "GET /api/nodes/Supplier/S900001": { ...f.supplierNode, id: "S900001", fields: { name: [f.fieldValue("Acme")] }, edges: [] },
      "GET /api/exposure/Supplier/S900001": f.supplierExposure,
      "GET /api/nodes/Supplier/S523036": { ...f.supplierNode, id: "S523036" },
      "GET /api/exposure/Supplier/S523036": f.supplierExposure,
      "POST /api/edits": { journal: { id: 3, ts: "", actor: "ana", action: "edit", summary: "", items: 1, undone_by: null }, edits: [], proposals_created: [] },
    });
    renderApp("/?sel=Supplier:S100228");
    await screen.findByRole("heading", { name: "Kessler Antriebe GmbH" });
    await userEvent.click(screen.getByRole("button", { name: "Edit node" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Create a node/ }));
    let dialog = await screen.findByRole("dialog", { name: "Create a node" });
    await userEvent.type(within(dialog).getByLabelText("Id"), "S900001");
    await userEvent.selectOptions(within(dialog).getByLabelText("Field 1"), "name");
    await userEvent.type(within(dialog).getByLabelText("Value 1"), "Acme");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await screen.findByRole("heading", { name: "Acme" });

    await userEvent.click(screen.getByRole("button", { name: "Delete value of Name" }));
    dialog = await screen.findByRole("dialog", { name: "Delete" });
    await userEvent.type(within(dialog).getByLabelText("Reason"), "typo");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "Edit node" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Merge into/ }));
    dialog = await screen.findByRole("dialog", { name: "Merge into another node" });
    await userEvent.type(within(dialog).getByLabelText("Into Supplier"), "Orbis");
    await userEvent.click(await within(dialog).findByRole("option", { name: /Orbis Plastics/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    expect(calls.filter((c) => c.method === "POST").map((c) => (c.body as { edits: unknown[] }).edits[0])).toEqual([
      { op: "create_node", type: "Supplier", id: "S900001", fields: { name: "Acme" } },
      { op: "delete", type: "Supplier", key: ["S900001"], field: "name", reason: "typo" },
      { op: "merge", type: "Supplier", from: "S900001", into: "S523036" },
    ]);
  });

  it("edits a component from the side panel: a field, an edge and a merge, each as one edit", async () => {
    localStorage.setItem("tierscope.author", "ana");
    const withKeys = {
      ...f.componentNode,
      edges: f.componentNode.edges.map((e) => ({ ...e, key: e.type === "SUPPLIES" ? ["S100228", "DMC-9948", "DMA-DE-01"] : ["DMC-9948", "DMA-DE-01"] })),
    };
    const { calls } = mockApi(f.overviewApi({
      "GET /api/nodes/Component/DMC-9948": withKeys,
      "GET /api/exposure/Component/DMC-9948": f.componentExposure,
      "GET /api/search": [{ type: "Component", id: "DMC-9949", name: "Gear housing, new", score: 0.9 }],
      "GET /api/nodes/Component/DMC-9949": { ...withKeys, id: "DMC-9949" },
      "GET /api/exposure/Component/DMC-9949": f.componentExposure,
      "POST /api/edits": { journal: { id: 3, ts: "", actor: "ana", action: "edit", summary: "", items: 1, undone_by: null }, edits: [], proposals_created: [] },
    }));
    renderApp("/?trail=Component:DMC-9948");
    const panel = await screen.findByRole("complementary", { name: "Node details" });
    await within(panel).findByRole("heading", { name: "Gear housing" });

    await userEvent.click(within(panel).getByRole("button", { name: "Change Description" }));
    let dialog = await screen.findByRole("dialog", { name: "Change a value" });
    await userEvent.clear(within(dialog).getByLabelText("New value"));
    await userEvent.type(within(dialog).getByLabelText("New value"), "Gear housing, cast");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(within(panel).getByRole("button", { name: /All relations \(2\)/ }));
    await userEvent.click(within(panel).getByRole("button", { name: "Delete edge DELIVERED_TO DMC-9948 → DMA-DE-01" }));
    dialog = await screen.findByRole("dialog", { name: "Delete" });
    await userEvent.type(within(dialog).getByLabelText("Reason"), "wrong plant");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(within(panel).getByRole("button", { name: "Edge" }));
    expect(await screen.findByRole("dialog", { name: "Add an edge" })).toHaveTextContent("Component DMC-9948");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await userEvent.click(within(panel).getByRole("button", { name: "Edit node" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Merge into/ }));
    dialog = await screen.findByRole("dialog", { name: "Merge into another node" });
    await userEvent.type(within(dialog).getByLabelText("Into Component"), "DMC-9949");
    await userEvent.click(await within(dialog).findByRole("option", { name: /Gear housing, new/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(search()).toBe("?trail=Component:DMC-9949")); // the merged-into part replaces it

    expect(calls.filter((c) => c.method === "POST").map((c) => (c.body as { edits: unknown[] }).edits[0])).toEqual([
      { op: "set_field", type: "Component", key: ["DMC-9948"], field: "description", value: "Gear housing, cast" },
      { op: "delete", type: "DELIVERED_TO", key: ["DMC-9948", "DMA-DE-01"], field: null, reason: "wrong plant" },
      { op: "merge", type: "Component", from: "DMC-9948", into: "DMC-9949" },
    ]);
  });
});
