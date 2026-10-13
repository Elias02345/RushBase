import { createApiClient, type HealthResponse } from "@rushbase/api-client";

// Absolute URL so the client also works where `Request` has no document base (tests, workers).
export const api = createApiClient({ baseUrl: new URL("/api", window.location.origin).href });

export type Health = HealthResponse;

/**
 * Fetch `/api/health`. The server answers 503 with a valid body while the database is
 * down, so that case still resolves; only an unreachable server rejects.
 */
export async function fetchHealth(): Promise<Health> {
  const { data, error } = await api.GET("/health");
  const body = data ?? error;
  if (!body) throw new Error("empty health response");
  return body;
}
