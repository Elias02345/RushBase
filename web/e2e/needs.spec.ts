import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.html ?? ""}`)).toEqual([]);
}

test("needs lists: open a list and confirm a candidate", async ({ page }) => {
  await login(page);
  await page.goto("/needs");
  await expectNoViolations(page);
  await page.getByRole("button", { name: /Imagefilm Hafen/ }).click();
  await expect(page.getByText("Fischerboote bei Sonnenaufgang")).toBeVisible();
  await expectNoViolations(page);
  await page.getByRole("button", { name: "Bestätigen" }).click();
  await expect(page.getByText("1 von 1 bestätigt")).toBeVisible();
});

test("MCP administration shows the connector URL and the call log", async ({ page }) => {
  await login(page);
  await page.goto("/admin?tab=mcp");
  await expect(page.getByText("https://media.example.org/mcp", { exact: true })).toBeVisible();
  await expect(page.getByRole("table", { name: "Aufrufprotokoll" })).toBeVisible();
  await expectNoViolations(page);
});
