import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import * as f from "./fixtures";
import { mockApi, renderApp } from "./utils";

function reviewApi() {
  const [weight, recovery, photo] = f.queue;
  return mockApi({
    "GET /api/stats": f.stats(3),
    "GET /api/proposals": f.queue,
    "GET /api/proposals/8": f.detail(weight),
    "GET /api/proposals/3": f.detail(photo, { impact: photo.impact }),
    "GET /api/proposals/2": f.detail(recovery),
    "POST /api/proposals/8/accept": () => f.decision(8, "Ana Kovalenko"),
    "POST /api/proposals/3/reject": () => f.decision(3, "Ana Kovalenko"),
  });
}

describe("First accept asks for a name", () => {
  it("asks once, sends the name, and does not ask again", async () => {
    const { calls } = reviewApi();
    renderApp("/review/8");
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    const dialog = await screen.findByRole("dialog", { name: "Your name" });
    expect(dialog).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false); // nothing sent before the name
    await userEvent.type(screen.getByLabelText("Name"), "Ana Kovalenko");
    await userEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await screen.findByText("Proposal #8 accepted");
    const accept = calls.find((c) => c.method === "POST")!;
    expect(accept.path).toBe("/api/proposals/8/accept");
    expect(accept.headers["X-Author"]).toBe("Ana Kovalenko");
    expect(localStorage.getItem("tierscope.author")).toBe("Ana Kovalenko");
    expect(screen.getByRole("button", { name: "Ana Kovalenko" })).toBeInTheDocument(); // shown in the header

    await userEvent.click(screen.getByRole("button", { name: /Photo/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await screen.findByText("Proposal #3 rejected");
    expect(screen.queryByRole("dialog", { name: "Your name" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.headers["X-Author"])).toEqual(["Ana Kovalenko", "Ana Kovalenko"]);
  });

  it("never asks just for viewing, and the name can be changed from the header", async () => {
    reviewApi();
    renderApp("/review/8");
    await screen.findByRole("button", { name: "Accept" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Set name" }));
    await userEvent.type(screen.getByLabelText("Name"), "Bo");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(localStorage.getItem("tierscope.author")).toBe("Bo");
    expect(screen.getByRole("button", { name: "Bo" })).toBeInTheDocument();
  });
});
