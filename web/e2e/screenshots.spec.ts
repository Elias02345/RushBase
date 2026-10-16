import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";

import { login } from "./helpers";

const OUT = resolve(import.meta.dirname, "../../docs/screenshots");

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  // Let skeletons, images and the dashboard rings finish their short transitions.
  await page.waitForTimeout(900);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`screenshots ${scheme}`, () => {
    test.use({ colorScheme: scheme, viewport: { width: 1360, height: 860 } });

    test(`app pages (${scheme})`, async ({ page }) => {
      const shot = async (name: string) => {
        await settle(page);
        await page.screenshot({ path: `${OUT}/${name}-${scheme}.png`, animations: "disabled" });
      };

      await page.addInitScript(() =>
        window.sessionStorage.setItem("rushbase.mock.scenario", "fresh"),
      );
      await page.goto("/setup");
      await page.getByRole("button", { name: "Weiter" }).click();
      await page.getByLabel("Benutzername").fill("elias");
      await page.getByLabel("Passwort", { exact: true }).fill("a-long-password-1");
      await page.getByLabel("Passwort wiederholen").fill("a-long-password-1");
      await page.getByRole("button", { name: "Weiter" }).click();
      await shot("setup-library");
      await page.getByRole("button", { name: "Bibliothek einrichten" }).click();
      await page.getByRole("button", { name: "Später in der Verwaltung" }).click();
      await expect(page.getByTestId("enroll-command")).toBeVisible();
      await shot("setup-done");

      // New context state: sign in as a regular user for the rest.
      await page.evaluate(() => window.sessionStorage.clear());
      await login(page);
      await shot("dashboard");

      await page.getByRole("link", { name: "Bibliothek" }).click();
      await expect(page.getByText(/\d+ Treffer/)).toBeVisible();
      await page
        .locator(".rb-tile__media")
        .nth(1)
        .hover({ position: { x: 150, y: 60 } });
      await shot("library");

      await page.locator(".rb-tile__media").nth(7).click();
      await expect(page.getByRole("tab", { name: "Technik" })).toBeVisible();
      await shot("asset");

      await page.getByRole("link", { name: /Verarbeitung/ }).click();
      await shot("processing");
      await page.getByRole("tab", { name: "Stapel" }).click();
      await shot("processing-batches");

      await page.getByRole("link", { name: "Verwaltung" }).click();
      await shot("admin-users");
      await page.getByRole("tab", { name: "Ablageregeln" }).click();
      await page.getByRole("button", { name: /Vorschau/ }).click();
      await expect(page.getByText(/Es wurde nichts verschoben/)).toBeVisible();
      await shot("admin-rules");
    });
  });
}

test("login screenshot", async ({ page }) => {
  await page.goto("/login");
  await settle(page);
  await page.screenshot({ path: `${OUT}/login-light.png` });
});
