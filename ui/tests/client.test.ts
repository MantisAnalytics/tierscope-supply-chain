import { describe, expect, it } from "vitest";
import { api, ApiError, query, storeAuthor } from "@/api/client";
import { invalidation, keys } from "@/api/queries";
import { jsonResponse, mockApi } from "./utils";

describe("fetch wrapper", () => {
  it("returns the parsed body and sends no author on reads", async () => {
    const { calls } = mockApi({ "GET /api/stats": { version: 3 } });
    storeAuthor("ana");
    expect(await api("/stats")).toEqual({ version: 3 });
    expect(calls[0].headers["X-Author"]).toBeUndefined();
  });

  it("sends the stored author with changes", async () => {
    const { calls } = mockApi({ "POST /api/proposals/8/accept": { ok: true } });
    storeAuthor("ana");
    await api("/proposals/8/accept", { json: { comment: "ok" } });
    expect(calls[0].headers["X-Author"]).toBe("ana");
    expect(calls[0].body).toEqual({ comment: "ok" });
  });

  it("turns a 409 into an error with the server's message", async () => {
    mockApi({ "POST /api/proposals/8/accept": () => jsonResponse(409, { detail: "proposal #8 is already accepted" }) });
    const error: ApiError = await api<never>("/proposals/8/accept", { json: {} }).catch((e: ApiError) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.message).toBe("proposal #8 is already accepted");
  });

  it("keeps the per-edit errors of a refused batch (422)", async () => {
    mockApi({
      "POST /api/edits": () =>
        jsonResponse(422, { detail: { message: "no edit applied", errors: [{ index: 1, message: "Supplier has no field 'colour'" }] } }),
    });
    const error: ApiError = await api<never>("/edits", { json: { edits: [] } }).catch((e: ApiError) => e);
    expect(error.status).toBe(422);
    expect(error.message).toBe("no edit applied");
    expect(error.errors).toEqual([{ index: 1, message: "Supplier has no field 'colour'" }]);
  });

  it("reads request validation errors", async () => {
    mockApi({ "GET /api/graph": () => jsonResponse(422, { detail: [{ loc: ["query", "depth"], msg: "less than or equal to 3" }] }) });
    const error: ApiError = await api<never>("/graph?depth=4").catch((e: ApiError) => e);
    expect(error.message).toBe("query.depth: less than or equal to 3");
  });

  it("builds query strings without empty values", () => {
    expect(query({ around: "Supplier:S1", depth: 2, relations: undefined, pending: false })).toBe("?around=Supplier%3AS1&depth=2&pending=false");
    expect(query({ q: "" })).toBe("");
  });

  it("invalidates every graph view after a change", () => {
    const prefixes = invalidation.graph.map((k) => k[0]);
    for (const key of [keys.overview, keys.network, keys.node("Site", "x"), keys.exposure("Site", "x"), keys.journal, keys.proposals("pending")]) {
      expect(prefixes).toContain(key[0]);
    }
    expect(invalidation.spec.map((k) => k[0])).toContain("specs");
  });
});
