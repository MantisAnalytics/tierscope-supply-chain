import { onlineManager } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { makeQueryClient } from "@/app/providers";
import { mockApi } from "./utils";

describe("offline machine", () => {
  afterEach(() => {
    onlineManager.setOnline(true);
    vi.restoreAllMocks();
  });

  it("still loads data from the local API when the browser reports no network", async () => {
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    onlineManager.setOnline(false);
    mockApi({ "GET /api/stats": { version: 3 }, "POST /api/proposals/8/accept": { ok: true } });
    const client = makeQueryClient();
    const data = await client.fetchQuery({ queryKey: ["stats"], queryFn: () => api("/stats") });
    expect(data).toEqual({ version: 3 });
    const mutation = client.getMutationCache().build(client, { mutationFn: () => api("/proposals/8/accept", { json: {} }) });
    await expect(mutation.execute(undefined)).resolves.toEqual({ ok: true });
  });
});
