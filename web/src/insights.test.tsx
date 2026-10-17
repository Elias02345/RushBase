import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { buildAssets } from "./api/mocks/data";
import { MOCK_PASSWORD } from "./api/mocks/handlers";
import { createQueryClient } from "./api/queryClient";
import { filterFor } from "./lib/heatmap";
import { AppProviders } from "./Providers";
import { createAppRouter } from "./router";
import { validateAdminSearch, validateLibrarySearch } from "./routes/search";

async function signIn(path: string, username = "elias") {
  const client: QueryClient = createQueryClient();
  const router = createAppRouter(client, createMemoryHistory({ initialEntries: [path] }));
  await act(async () => {
    render(
      <AppProviders client={client}>
        <RouterProvider router={router} />
      </AppProviders>,
    );
  });
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Benutzername"), username);
  await user.type(screen.getByLabelText("Passwort"), MOCK_PASSWORD);
  await user.click(screen.getByRole("button", { name: "Anmelden" }));
  return user;
}

describe("route and filter helpers", () => {
  it("accepts the new admin tabs and the project filter", () => {
    expect(validateAdminSearch({ tab: "audit" })).toEqual({ tab: "audit" });
    expect(validateAdminSearch({ tab: "tokens" })).toEqual({ tab: "tokens" });
    expect(validateLibrarySearch({ used_in_project: "Imagefilm Hafen" })).toEqual({
      used_in_project: "Imagefilm Hafen",
    });
  });

  it("turns heatmap cells into library filters", () => {
    const technical = filterFor({}, { key: "orientation", kind: "technical" }, "landscape");
    expect(technical).toEqual({ orientation: "landscape" });
    const both = filterFor(technical, { key: "location", kind: "taxonomy" }, "harbour");
    expect(both).toEqual({ orientation: "landscape", tag: ["location:harbour"] });
  });
});

describe("asset detail", () => {
  it("shows the projects an asset was used in and its linked needs lists", async () => {
    const asset = buildAssets().find((a) => a.media_type === "video" && a.status === "filed");
    const user = await signIn(`/assets/${asset?.id}`);
    await user.click(await screen.findByRole("tab", { name: "Verwendung" }));
    expect(await screen.findByText("Messe-Teaser")).toBeInTheDocument();
    expect(screen.getAllByText("Imagefilm Hafen").length).toBeGreaterThan(0);
    await user.type(screen.getByLabelText("Projektname"), "Sommerkampagne");
    await user.click(screen.getByRole("button", { name: "Eintragen" }));
    expect(await screen.findByText("Sommerkampagne")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Bedarfslisten" }));
    expect(await screen.findByText("Kran im Gegenlicht")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Bestätigen" }));
    expect(await screen.findByText("Bestätigt")).toBeInTheDocument();
  });
});

describe("dashboard", () => {
  it("draws growth, throughput and a coverage heatmap whose cells open the library", async () => {
    await signIn("/");
    expect(await screen.findByText("Speicherwachstum")).toBeInTheDocument();
    expect(await screen.findByText(/Aufträge pro Stunde/)).toBeInTheDocument();
    const cells = await screen.findAllByRole("link", { name: /Medien$/ });
    const landscape = cells.find((c) => {
      const href = c.getAttribute("href") ?? "";
      return href.includes("orientation=landscape") && href.includes("media_type=video");
    });
    expect(landscape).toBeDefined();
    const weakest = screen.getByRole("region", { name: "Schwächste Abdeckung" });
    expect(within(weakest).getAllByRole("link").length).toBeGreaterThan(0);
  });
});

describe("processing", () => {
  it("counts what a reprocess would queue before it queues anything", async () => {
    const user = await signIn("/processing");
    await user.click(await screen.findByRole("button", { name: "Neu verarbeiten" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Aktuelle Modelle je Stufe")).toBeInTheDocument();
    expect((await within(dialog).findAllByText(/Version 1\.2/)).length).toBeGreaterThan(0);
    const queue = within(dialog).getByRole("button", { name: "Erst zählen" });
    expect(queue).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Wert"), "1.2");
    await user.click(within(dialog).getByRole("button", { name: "Zählen" }));
    expect(await within(dialog).findByText("52 Medien betroffen")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "52 neu einplanen" })).toBeEnabled();
  });
});

describe("administration", () => {
  it("lists personal tokens with their expiry and shows a new one once", async () => {
    const user = await signIn("/admin?tab=tokens");
    expect(await screen.findByText("Schnittplatz")).toBeInTheDocument();
    expect(screen.getByText("läuft nie ab")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "Laptop");
    await user.click(screen.getByRole("button", { name: "Token erstellen" }));
    expect(await screen.findByText(/rbp_mock-token/)).toBeInTheDocument();
  });

  it("shows the audit log and opens an entry with its details", async () => {
    const user = await signIn("/admin?tab=audit");
    const table = await screen.findByRole("table", { name: "Protokoll" });
    const rows = await within(table).findAllByRole("row");
    expect(rows.length).toBeGreaterThan(5);
    await user.click(rows[2] as HTMLElement);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("Einzelheiten")).toBeInTheDocument();
    expect(within(drawer).getByText(/"note": "mock entry"/)).toBeInTheDocument();
  });
});
