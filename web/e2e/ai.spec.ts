import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.html ?? ""}`)).toEqual([]);
}

test("hybrid search toggles between assets and scenes", async ({ page }) => {
  await login(page);
  await page.goto("/library?q=Fischerboote");
  const results = page.getByRole("list", { name: "Treffer" });
  await expect(results.getByRole("link").first()).toBeVisible();
  await expectNoViolations(page);
  await page.getByRole("button", { name: "Szenen" }).click();
  await expect(page).toHaveURL(/level=shot/);
  await expect(results.getByRole("link").first()).toContainText(/\d:\d\d/);
  await expectNoViolations(page);
});

test("asset detail shows description, tags, transcript and similar material", async ({ page }) => {
  await login(page);
  await page.goto("/library?q=Fischerboote");
  await page.getByRole("list", { name: "Treffer" }).getByRole("link").first().click();
  await expect(page).toHaveURL(/\/assets\//);

  await page.getByRole("tab", { name: "Beschreibung" }).click();
  await expect(page.getByText("Fischerboote liegen im Hafen").first()).toBeVisible();
  await page
    .getByRole("group", { name: "Sprache der Beschreibung" })
    .getByRole("button", { name: "EN" })
    .click();
  await expect(page.getByText(/Fishing boats/).first()).toBeVisible();
  await expectNoViolations(page);

  await page.getByRole("tab", { name: "Tags" }).click();
  await expect(page.getByRole("heading", { name: "Ort" })).toBeVisible();
  await expect(page.getByText("Von Hand").or(page.getByText("KI")).first()).toBeVisible();
  await expectNoViolations(page);

  await page.getByRole("tab", { name: "Transkript" }).click();
  await expectNoViolations(page);

  await page.getByRole("button", { name: "Ähnliche finden" }).click();
  await expect(page.getByRole("list", { name: "Ähnliches Material" })).toBeVisible();
  await expectNoViolations(page);
});

test("review queue: approve with the keyboard", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Prüfen" }).click();
  await expect(page.getByRole("heading", { name: "Prüfen", level: 1 })).toBeVisible();
  const lead = page.locator(".page__lead");
  await expect(lead).toContainText(/\d+ Objekt/);
  const before = Number(((await lead.textContent()) ?? "").match(/\d+/)?.[0]);
  expect(before).toBeGreaterThan(0);
  await expectNoViolations(page);
  await page.keyboard.press("a");
  await expect(lead).toContainText(String(before - 1));
});

test("taxonomy: approve a proposal and browse the fields", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Taxonomie" }).click();
  await expect(page.getByRole("heading", { name: "Taxonomie", level: 1 })).toBeVisible();
  const proposals = page.getByRole("list", { name: "Vorschläge" });
  await expect(proposals.getByText("pier")).toBeVisible();
  await expectNoViolations(page);
  await proposals.getByRole("button", { name: "Freigeben" }).first().click();
  await expect(proposals.getByText("pier")).toHaveCount(0);
  await page.getByRole("tab", { name: "Felder und Werte" }).click();
  await expect(page.getByRole("heading", { name: "Ort" })).toBeVisible();
  await page.getByRole("button", { name: "Zusammenführen" }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expectNoViolations(page);
});

test("golden set: label with number keys", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Referenzset" }).click();
  await expect(page.getByRole("heading", { name: "Referenzset", level: 1 })).toBeVisible();
  await expect(page.getByRole("group", { name: "Einstellungsgröße" })).toBeVisible();
  await expectNoViolations(page);
  await page.keyboard.press("2");
  await expect(
    page.getByRole("group", { name: "Einstellungsgröße" }).getByRole("button", { pressed: true }),
  ).toHaveCount(1);
});

test("viewers do not see editor pages", async ({ page }) => {
  await login(page, "jonas");
  await expect(page.getByRole("link", { name: "Prüfen" })).toHaveCount(0);
  await page.goto("/review");
  await expect(page).not.toHaveURL(/\/review/);
});
