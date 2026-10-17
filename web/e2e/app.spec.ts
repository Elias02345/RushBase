import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.html ?? ""}`)).toEqual([]);
}

test("redirects to the login page and rejects a wrong password", async ({ page }) => {
  await page.goto("/library");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Benutzername").fill("elias");
  await page.getByLabel("Passwort").fill("wrong-password");
  await page.getByRole("button", { name: "Anmelden" }).click();
  await expect(page.getByText("Benutzername oder Passwort stimmt nicht.")).toBeVisible();
  await expectNoViolations(page);
});

test("login, dashboard, library search and filter, asset detail, processing", async ({ page }) => {
  await login(page);

  // Dashboard with the pipeline visual.
  await expect(page.getByRole("group", { name: "Eingang, Analyse und Inhalt" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Worker" })).toBeVisible();
  await expectNoViolations(page);

  // Library: search narrows the grid, URL carries the filter.
  await page.getByRole("link", { name: "Bibliothek" }).click();
  await expect(page.getByText(/\d+ Treffer/)).toBeVisible();
  const search = page.getByRole("searchbox", { name: "Material durchsuchen" });
  await search.fill("Harbour");
  await expect(page).toHaveURL(/q=Harbour/);
  await expect(page.getByRole("list", { name: "Treffer" }).getByRole("link").first()).toBeVisible();
  await search.fill("");

  // Facet filter, then keyboard focus on the first tile.
  await page.getByRole("button", { name: /^Foto \d/ }).click();
  await expect(page).toHaveURL(/media_type=photo/);
  await expectNoViolations(page);
  await page.getByRole("button", { name: /^Foto \d/ }).click();

  // Open a video via keyboard and look at the detail page.
  await page.getByRole("button", { name: /^Video \d/ }).click();
  const firstTile = page.locator(".rb-tile__media").first();
  await firstTile.focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/assets\//);
  await expect(page.getByRole("tab", { name: "Technik" })).toBeVisible();
  await page.getByRole("tab", { name: "Dateien" }).click();
  await expect(page.getByRole("button", { name: "Pfad kopieren" }).first()).toBeVisible();
  await expectNoViolations(page);

  // Processing.
  await page.getByRole("link", { name: /Verarbeitung/ }).click();
  await expect(page.getByRole("heading", { name: "Verarbeitung", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: "Stapel" }).click();
  await expect(page.getByRole("table", { name: "Stapel" })).toBeVisible();
  await page.getByRole("tab", { name: "Quarantäne" }).click();
  await expect(page.getByRole("button", { name: "Erneut versuchen" }).first()).toBeVisible();
  await expectNoViolations(page);
});

test("command palette and shortcuts", async ({ page }) => {
  await login(page);
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder("Wohin soll es gehen?").fill("biblio");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/library/);
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "Tastenkürzel" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.keyboard.press("/");
  await expect(page.getByRole("searchbox", { name: "Material durchsuchen" })).toBeFocused();
});

test("viewers do not see the admin area", async ({ page }) => {
  await login(page, "jonas");
  await expect(page.getByRole("link", { name: "Verwaltung" })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page).not.toHaveURL(/\/admin/);
});

test("admin can create a worker token and preview filing rules", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Verwaltung" }).click();
  await page.getByRole("tab", { name: "Worker-Tokens" }).click();
  await page.getByRole("button", { name: "Token erstellen" }).click();
  await expect(page.getByRole("dialog", { name: "Token erstellt" })).toContainText(
    "rushbase enroll",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Ablageregeln" }).click();
  await page.getByRole("button", { name: /Vorschau/ }).click();
  await expect(page.getByText(/Es wurde nichts verschoben/)).toBeVisible();
  await expectNoViolations(page);
});

test("setup wizard walks through to the worker command", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("rushbase.lang", "de");
    window.sessionStorage.setItem("rushbase.mock.scenario", "fresh");
  });
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup/);
  await page.getByRole("button", { name: "Weiter" }).click();
  await page.getByLabel("Setup-Code").fill("setup-code-from-installer");
  await page.getByLabel("Benutzername").fill("admin");
  await page.getByLabel("Passwort", { exact: true }).fill("a-long-password-1");
  await page.getByLabel("Passwort wiederholen").fill("a-long-password-1");
  await page.getByRole("button", { name: "Weiter" }).click();
  await page.getByRole("button", { name: "Bibliothek einrichten" }).click();
  await page.getByRole("button", { name: "Später in der Verwaltung" }).click();
  await expect(page.getByTestId("enroll-command")).toContainText("rushbase enroll --server");
  await expectNoViolations(page);
});

test("setup wizard prefills the setup code from the URL", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("rushbase.lang", "de");
    window.sessionStorage.setItem("rushbase.mock.scenario", "fresh");
  });
  await page.goto("/setup?token=prefilled-code-123");
  await page.getByRole("button", { name: "Weiter" }).click();
  await expect(page.getByLabel("Setup-Code")).toHaveValue("prefilled-code-123");
  await expectNoViolations(page);
});

test("dark mode passes the contrast checks", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await login(page);
  await expectNoViolations(page);
  await page.getByRole("link", { name: "Bibliothek" }).click();
  await expect(page.getByText(/\d+ Treffer/)).toBeVisible();
  await expectNoViolations(page);
});
