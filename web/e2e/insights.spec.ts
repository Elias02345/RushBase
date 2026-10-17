import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { buildAssets } from "../src/api/mocks/data";
import { login } from "./helpers";

async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.html ?? ""}`)).toEqual([]);
}

test("dashboard: growth, throughput and a coverage heatmap with clickable cells", async ({
  page,
}) => {
  await login(page);
  await expect(page.getByRole("heading", { name: "Speicherwachstum" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Durchsatz" })).toBeVisible();
  await expect(page.getByRole("table", { name: /Anzahl Medien nach/ })).toBeAttached();
  await expectNoViolations(page);
  await page
    .getByRole("link", { name: /^Video, Querformat: \d+ Medien$/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/library\?.*media_type=video/);
  await expect(page).toHaveURL(/orientation=landscape/);
});

test("dashboard: the heatmap axes can be changed to taxonomy fields", async ({ page }) => {
  await login(page);
  await page.getByLabel("Zeilen").click();
  await page.getByRole("option", { name: "Ort" }).click();
  await expect(page.getByRole("rowheader", { name: "harbour" })).toBeVisible();
  await expectNoViolations(page);
});

test("processing: reprocess dialog counts before it queues", async ({ page }) => {
  await login(page);
  await page.goto("/processing");
  await page.getByRole("button", { name: "Neu verarbeiten" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Aktuelle Modelle je Stufe")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Erst zählen" })).toBeDisabled();
  await dialog.getByLabel("Wert").fill("1.2");
  await dialog.getByRole("button", { name: "Zählen", exact: true }).click();
  await expect(dialog.getByText("52 Medien betroffen")).toBeVisible();
  await expectNoViolations(page);
  await dialog.getByRole("button", { name: "52 neu einplanen" }).click();
  await expect(
    page.locator(".rb-toast__title", { hasText: "52 Aufträge eingeplant" }),
  ).toBeVisible();
});

test("admin: audit log filters and detail drawer", async ({ page }) => {
  await login(page);
  await page.goto("/admin?tab=audit");
  const table = page.getByRole("table", { name: "Protokoll" });
  await expect(table.getByRole("row").nth(2)).toBeVisible();
  await expectNoViolations(page);
  await page.getByLabel("Zielart").fill("asset");
  await expect(table.getByText("asset.trash").first()).toBeVisible();
  await table.getByRole("row").nth(1).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByText("Einzelheiten")).toBeVisible();
  await expectNoViolations(page);
});

test("admin: personal tokens with expiry", async ({ page }) => {
  await login(page);
  await page.goto("/admin?tab=tokens");
  await expect(page.getByText("Schnittplatz")).toBeVisible();
  await expectNoViolations(page);
  await page.getByLabel("Name").fill("Laptop");
  await page.getByLabel("Gültigkeit").click();
  await page.getByRole("option", { name: "90 Tage" }).click();
  await page.getByRole("button", { name: "Token erstellen" }).click();
  await expect(page.getByText(/rbp_mock-token/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Laptop")).toBeVisible();
});

test("asset detail: usage tab and linked needs lists", async ({ page }) => {
  await login(page);
  await page.goto("/library?used_in_project=Imagefilm%20Hafen");
  await expect(
    page.getByRole("button", { name: /Verwendet in Projekt: Imagefilm Hafen/ }),
  ).toBeVisible();
  await page.locator(".rb-tile__media").first().focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/assets\//);
  await page.getByRole("tab", { name: "Verwendung" }).click();
  await expect(page.getByText("Imagefilm Hafen").first()).toBeVisible();

  // The first filed video also is a candidate of a needs list.
  const asset = buildAssets().find((a) => a.media_type === "video" && a.status === "filed");
  await page.goto(`/assets/${asset?.id}`);
  await page.getByRole("tab", { name: "Verwendung" }).click();
  await expect(page.getByText("Messe-Teaser").first()).toBeVisible();
  await expectNoViolations(page);
  await page.getByRole("tab", { name: "Bedarfslisten" }).click();
  await expect(page.getByText("Kran im Gegenlicht")).toBeVisible();
  await page.getByRole("button", { name: "Ablehnen" }).click();
  await expect(page.getByText("Abgelehnt")).toBeVisible();
});
