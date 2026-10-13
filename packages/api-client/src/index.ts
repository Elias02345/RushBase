import createClient, { type ClientOptions } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type HealthResponse = components["schemas"]["HealthResponse"];

/**
 * Create a typed client for the Rushbase API.
 * `baseUrl` defaults to `/api`, which the Vite dev proxy and the production reverse proxy
 * forward to the server.
 */
export function createApiClient(options: ClientOptions = {}) {
  // Resolve `fetch` per request (not at creation) so tests and polyfills can replace it.
  return createClient<paths>({
    baseUrl: "/api",
    fetch: (request) => globalThis.fetch(request),
    ...options,
  });
}
