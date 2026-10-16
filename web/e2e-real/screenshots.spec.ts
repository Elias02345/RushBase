import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

// Retakes docs/screenshots/{dashboard,library,asset,processing}-{light,dark}.png from the real
// stack. Runs after real.spec.ts (same server, already processed) when RUSHBASE_E2E_SCREENSHOTS=1.
const OUT = resolve(import.meta.dirname, "../../docs/screenshots");
const PASSWORD = "a-long-password-1";

test.skip(process.env.RUSHBASE_E2E_SCREENSHOTS !== "1", "set RUSHBASE_E2E_SCREENSHOTS=1");

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1200);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`real screenshots ${scheme}`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1360, height: 860 } });

    test(`app pages (${scheme})`, async ({ page }) => {
      const shot = async (name: string) => {
        await settle(page);
        await page.screenshot({ path: `${OUT}/${name}-${scheme}.png`, animations: "disabled" });
      };
      await page.addInitScript(() => window.localStorage.setItem("rushbase.lang", "de"));
      await page.goto("/login");
      await page.getByLabel("Benutzername").fill("admin");
      await page.getByLabel("Passwort").fill(PASSWORD);
      await page.getByRole("button", { name: "Anmelden" }).click();
      await page.getByRole("heading", { level: 1 }).waitFor();
      await shot("dashboard");

      await page.getByRole("link", { name: "Bibliothek" }).click();
      await expect(page.getByText(/\d+ Treffer/)).toBeVisible();
      await page.locator(".rb-tile__media").nth(1).hover({ position: { x: 150, y: 60 } });
      await shot("library");

      await page.getByRole("button", { name: /^Video \d/ }).click();
      await page.locator(".rb-tile__media").first().click();
      await expect(page.locator("video")).toBeVisible();
      await expect(page.getByRole("tab", { name: "Technik" })).toBeVisible();
      await shot("asset");

      await page.getByRole("link", { name: /Verarbeitung/ }).click();
      await shot("processing");
    });
  });
}
