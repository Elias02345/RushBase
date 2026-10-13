import { ThemeProvider } from "@rushbase/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { readiness } from "./readiness";
import { createAppRouter } from "./router";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function renderApp() {
  const router = createAppRouter();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(
      <ThemeProvider>
        <QueryClientProvider client={client}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </ThemeProvider>,
    );
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readiness", () => {
  it("maps server and database state to a percentage", () => {
    expect(readiness(false, false)).toBe(0);
    expect(readiness(true, false)).toBe(50);
    expect(readiness(true, true)).toBe(100);
  });
});

describe("landing page", () => {
  it("shows a healthy server in German by default", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "ok", version: "0.1.0", database: true })),
    );
    await renderApp();
    expect(await screen.findByText("Version 0.1.0")).toBeInTheDocument();
    expect(screen.getByText("erreichbar")).toBeInTheDocument();
    expect(screen.getByText("verbunden")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });

  it("reports a degraded database from a 503 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ status: "degraded", version: "0.1.0", database: false }, 503),
      ),
    );
    await renderApp();
    expect(await screen.findByText("keine Verbindung")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });

  it("reports an unreachable server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("network down"))),
    );
    await renderApp();
    expect(await screen.findByText("nicht erreichbar")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });

  it("switches language and theme", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ status: "ok", version: "0.1.0", database: true })),
    );
    await renderApp();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Sprache wechseln" }));
    await waitFor(() => expect(screen.getByText("Server status")).toBeInTheDocument());
    expect(document.documentElement.lang).toBe("en");

    await user.click(screen.getByRole("button", { name: "Toggle colour theme" }));
    expect(["light", "dark"]).toContain(document.documentElement.dataset.theme);
  });
});
