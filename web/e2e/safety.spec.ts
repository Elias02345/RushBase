import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.html ?? ""}`)).toEqual([]);
}

async function firstAssetId(page: Page, mediaType: string): Promise<string> {
  return page.evaluate(async (type) => {
    const response = await fetch(`/api/v1/assets?media_type=${type}&limit=1`);
    return ((await response.json()) as { items: { id: string }[] }).items[0]?.id ?? "";
  }, mediaType);
}

async function chooseOption(page: Page, label: string | RegExp, option: string | RegExp) {
  await page.getByRole("combobox", { name: label }).click();
  await page.getByRole("option", { name: option }).first().click();
}

test("delete to trash with a confirmation dialog, then restore", async ({ page }) => {
  await login(page);
  await page.goto(`/assets/${await firstAssetId(page, "video")}`);
  await page.getByRole("button", { name: "In den Papierkorb" }).click();
  const dialog = page.getByRole("dialog", { name: "In den Papierkorb verschieben?" });
  await expect(dialog).toBeVisible();
  await expectNoViolations(page);
  await dialog.getByRole("button", { name: "In den Papierkorb verschieben" }).click();

  await expect(page).toHaveURL(/tab=trash/);
  const table = page.getByRole("table", { name: "Papierkorb" });
  await expect(table.getByRole("button", { name: "Wiederherstellen" })).toHaveCount(1);
  await expectNoViolations(page);
  await table.getByRole("button", { name: "Wiederherstellen" }).click();
  await expect(page.getByText("Der Papierkorb ist leer")).toBeVisible();
});

test("privacy: find and lock the assets of a batch", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Datenschutz" }).click();
  await expect(page.getByRole("heading", { name: "Datenschutz", level: 1 })).toBeVisible();
  await page.getByRole("tab", { name: "Batch" }).click();
  await chooseOption(page, "Batch", /./);
  await page.getByRole("button", { name: "Treffer anzeigen" }).click();
  await expect(page.getByText(/\d+ Assets gefunden/)).toBeVisible();
  await expectNoViolations(page);
  await page.getByRole("button", { name: /Assets sperren/ }).click();
  await expect(page.getByText(/\d+ Assets gesperrt/).first()).toBeVisible();
});

test("asset privacy tab edits consent", async ({ page }) => {
  await login(page);
  await page.goto(`/assets/${await firstAssetId(page, "video")}`);
  await page.getByRole("tab", { name: "Datenschutz" }).click();
  await chooseOption(page, "Einwilligung", "Ja");
  await page.getByLabel("Nachweis (Verweis)").fill("Vertrag 2026-14");
  await page.getByRole("button", { name: "Speichern" }).click();
  await expect(page.getByText("Gespeichert").first()).toBeVisible();
  await expectNoViolations(page);
});

test("audio sync suggestion can be confirmed", async ({ page }) => {
  await login(page);
  await page.goto(`/assets/${await firstAssetId(page, "audio")}`);
  await page.getByRole("tab", { name: "Ton-Sync" }).click();
  await expect(page.getByText(/Versatz \+5\.00 s/)).toBeVisible();
  await page.getByRole("button", { name: "Bestätigen" }).click();
  await expect(page.getByText("Bestätigt", { exact: true })).toBeVisible();
  await expectNoViolations(page);
});

test("migration tool is off by default and needs a typed confirmation", async ({ page }) => {
  await login(page);
  await page.goto("/admin?tab=migration");
  await expect(page.getByText("Neue Ablageregel")).toHaveCount(0);
  await page.getByRole("switch", { name: "Migrationswerkzeug aktivieren" }).click();
  await chooseOption(page, "Batch", /./);
  await page.getByLabel("Neue Ablageregel").fill("Content/{media_type}/{batch_label}/");
  await page.getByRole("button", { name: "Vorschau" }).click();
  await expect(page.getByText(/\d+ Assets würden verschoben/)).toBeVisible();
  await expectNoViolations(page);
  const execute = page.getByRole("button", { name: "Jetzt verschieben" });
  await expect(execute).toBeDisabled();
  const prompt = (await page.getByText(/Zur Bestätigung/).textContent()) ?? "";
  await page.getByLabel(/Zur Bestätigung/).fill(prompt.match(/MIGRATE \d+/)?.[0] ?? "");
  await execute.click();
  await expect(
    page.getByRole("button", { name: "Relink-Liste (CSV) herunterladen" }),
  ).toBeVisible();
});
