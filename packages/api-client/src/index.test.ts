import { describe, expect, it, vi } from "vitest";

import { createApiClient } from "./index";

describe("createApiClient", () => {
  it("calls /api/health with the typed client", async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      expect(new URL(request.url, "http://localhost").pathname).toBe("/api/health");
      return new Response(JSON.stringify({ status: "ok", version: "0.1.0", database: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    const client = createApiClient({ baseUrl: "http://localhost/api", fetch: fetchMock });
    const { data } = await client.GET("/health");
    expect(data).toEqual({ status: "ok", version: "0.1.0", database: true });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
