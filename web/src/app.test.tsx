import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import de from "./locales/de.json";
import en from "./locales/en.json";
import { MOCK_PASSWORD } from "./api/mocks/handlers";
import { createQueryClient } from "./api/queryClient";
import { AppProviders } from "./Providers";
import { formatBytes, formatDuration, formatResolution } from "./lib/format";
import { createAppRouter } from "./router";
import { validateLibrarySearch, validateLoginSearch } from "./routes/search";

async function renderApp(path: string) {
  const client: QueryClient = createQueryClient();
  const router = createAppRouter(client, createMemoryHistory({ initialEntries: [path] }));
  await act(async () => {
    render(
      <AppProviders client={client}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
  });
  return { router, user: userEvent.setup() };
}

async function signIn(path: string, username = "elias") {
  const app = await renderApp(path);
  await app.user.type(await screen.findByLabelText("Benutzername"), username);
  await app.user.type(screen.getByLabelText("Passwort"), MOCK_PASSWORD);
  await app.user.click(screen.getByRole("button", { name: "Anmelden" }));
  return app;
}

describe("formatting", () => {
  it("formats sizes, durations and resolutions", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toMatch(/1,54 KB/);
    expect(formatDuration(83_250)).toBe("1:23");
    expect(formatDuration(3_725_000)).toBe("1:02:05");
    expect(formatResolution(3840, 2160)).toBe("4K");
    expect(formatResolution(1080, 1920)).toBe("1080p");
    expect(formatResolution(null, null)).toBeNull();
  });
});

describe("URL search validation", () => {
  it("keeps valid filters and drops garbage", () => {
    expect(
      validateLibrarySearch({
        q: "lake",
        media_type: "video",
        status: "nope",
        date_from: "2026-01-02",
        view: "list",
      }),
    ).toEqual({ q: "lake", media_type: "video", date_from: "2026-01-02", view: "list" });
  });

  it("only accepts same-origin redirects", () => {
    expect(validateLoginSearch({ redirect: "/library?q=x" })).toEqual({ redirect: "/library?q=x" });
    expect(validateLoginSearch({ redirect: "//evil.example" })).toEqual({});
    expect(validateLoginSearch({ redirect: "https://evil.example" })).toEqual({});
  });
});

describe("translations", () => {
  const flatten = (node: unknown, prefix = ""): string[] =>
    typeof node === "object" && node !== null
      ? Object.entries(node).flatMap(([k, v]) => flatten(v, prefix ? `${prefix}.${k}` : k))
      : [prefix];
  const strip = (key: string) => key.replace(/_(one|other)$/, "");

  it("German and English define the same keys", () => {
    expect(new Set(flatten(de).map(strip))).toEqual(new Set(flatten(en).map(strip)));
  });

  it("defines every statically referenced key", () => {
    const keys = new Set(flatten(de).map(strip));
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) {
          if (name !== "locales" && name !== "mocks") walk(full);
        } else if (/\.tsx?$/.test(name) && !name.includes(".test.")) files.push(full);
      }
    };
    walk(import.meta.dirname);
    const used = files.flatMap((file) =>
      [...readFileSync(file, "utf8").matchAll(/\bt\(\s*"([A-Za-z0-9_.]+)"/g)].map(
        (m) => m[1] as string,
      ),
    );
    const missing = used.filter((key) => !keys.has(key));
    expect(missing).toEqual([]);
  });
});

describe("app", () => {
  it("sends anonymous visitors to the login page", async () => {
    await renderApp("/library");
    expect(await screen.findByRole("heading", { name: "Willkommen zurück" })).toBeInTheDocument();
  });

  it("shows an error for a wrong password", async () => {
    const app = await renderApp("/login");
    await app.user.type(await screen.findByLabelText("Benutzername"), "elias");
    await app.user.type(screen.getByLabelText("Passwort"), "nope");
    await app.user.click(screen.getByRole("button", { name: "Anmelden" }));
    expect(await screen.findByText("Benutzername oder Passwort stimmt nicht.")).toBeInTheDocument();
  });

  it("signs in and shows the dashboard pipeline with live counts", async () => {
    await signIn("/");
    const pipeline = await screen.findByRole("group", { name: "Eingang, Analyse und Inhalt" });
    expect(within(pipeline).getByText("Einsortiert")).toBeInTheDocument();
    expect(await screen.findByText("studio-pc")).toBeInTheDocument();
  });

  it("explains the no-worker case in a friendly way", async () => {
    window.sessionStorage.setItem("rushbase.mock.scenario", "no-workers");
    await signIn("/");
    expect(
      await screen.findByText(/Neues Material wartet, bis dein Verarbeitungs-PC startet/),
    ).toBeInTheDocument();
  });

  it("searches the library and reflects the query in the URL", async () => {
    const app = await signIn("/library");
    const field = await screen.findByRole("searchbox", { name: "Material durchsuchen" });
    await screen.findByText(/260 Treffer/);
    await app.user.type(field, "Harbour");
    await waitFor(() => expect(app.router.state.location.search).toMatchObject({ q: "Harbour" }), {
      timeout: 3000,
    });
    await waitFor(() => expect(screen.queryByText(/260 Treffer/)).not.toBeInTheDocument());
  });

  it("hides the admin area from viewers", async () => {
    await signIn("/", "jonas");
    await screen.findByRole("group", { name: "Eingang, Analyse und Inhalt" });
    expect(screen.queryByRole("link", { name: "Verwaltung" })).not.toBeInTheDocument();
  });

  it("opens the command palette with Ctrl+K", async () => {
    const app = await signIn("/");
    await screen.findByRole("group", { name: "Eingang, Analyse und Inhalt" });
    await app.user.keyboard("{Control>}k{/Control}");
    expect(await screen.findByPlaceholderText("Wohin soll es gehen?")).toBeInTheDocument();
  });
});
