import { createApiClient } from "@rushbase/api-client";
import type { Middleware } from "openapi-fetch";

/** Cookie and header names must match `server/src/rushbase_server/api/deps.py`. */
export const CSRF_COOKIE = "rb_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** A failed API call with the server's `detail` message when it sent one. */
export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function readCookie(name: string): string {
  for (const part of document.cookie.split("; ")) {
    const [key, ...rest] = part.split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return "";
}

/** Double-submit CSRF: unsafe requests echo the readable CSRF cookie in a header. */
const csrf: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method.toUpperCase())) {
      const token = readCookie(CSRF_COOKIE);
      if (token) request.headers.set(CSRF_HEADER, token);
    }
    return request;
  },
};

function origin(): string {
  return window.location.origin;
}

/** Typed client for the whole server API (generated from its OpenAPI schema). */
export function createGeneratedClient() {
  const client = createApiClient({ baseUrl: origin(), credentials: "same-origin" });
  client.use(csrf);
  return client;
}

export const api = createGeneratedClient();

interface Result<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

function detailOf(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first: unknown = detail[0];
      if (first && typeof first === "object" && "msg" in first) {
        return String((first as { msg: unknown }).msg);
      }
    }
  }
  return "";
}

/** Resolve to `data` or throw an `ApiError`; use inside query and mutation functions. */
export async function unwrap<T>(request: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok) return data as T;
  const retry = Number(response.headers.get("retry-after"));
  throw new ApiError(
    response.status,
    detailOf(error) || response.statusText,
    Number.isFinite(retry) && retry > 0 ? retry : null,
  );
}
