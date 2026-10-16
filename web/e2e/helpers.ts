import type { Page } from "@playwright/test";

export const PASSWORD = "rushbase-demo-pass";

/** Open the app in German and sign in as one of the mock users. */
export async function login(page: Page, username = "elias", scenario?: string): Promise<void> {
  await page.addInitScript((value) => {
    window.localStorage.setItem("rushbase.lang", "de");
    if (value) window.sessionStorage.setItem("rushbase.mock.scenario", value);
  }, scenario ?? null);
  await page.goto("/login");
  await page.getByLabel("Benutzername").fill(username);
  await page.getByLabel("Passwort").fill(PASSWORD);
  await page.getByRole("button", { name: "Anmelden" }).click();
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
  await page.getByRole("heading", { level: 1 }).waitFor();
}
