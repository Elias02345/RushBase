import { expect, test, type Page } from "@playwright/test";

const OUT = "../docs/screenshots";

async function open(page: Page, scenario: string, scheme: "light" | "dark", hash = "") {
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
  await page.goto(`/?scenario=${scenario}${hash}`);
}

for (const scheme of ["light", "dark"] as const) {
  test(`processor idle (${scheme})`, async ({ page }) => {
    await open(page, "idle", scheme);
    await expect(page.getByRole("heading", { level: 1, name: /142 Dateien/ })).toBeVisible();
    await expect(page.getByText("NVIDIA GeForce RTX 3070")).toBeVisible();
    await page.screenshot({ path: `${OUT}/desktop-processor-idle-${scheme}.png` });
  });

  test(`processor running (${scheme})`, async ({ page }) => {
    await open(page, "running", scheme);
    await expect(page.getByRole("button", { name: /Pause \/ Stopp/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(/[3-9] von 828 Aufträgen/, {
      timeout: 10_000,
    });
    await page.screenshot({ path: `${OUT}/desktop-processor-running-${scheme}.png` });
  });

  test(`first run wizard (${scheme})`, async ({ page }) => {
    await open(page, "first-run", scheme);
    await expect(page.getByRole("heading", { name: "Mit dem Server verbinden" })).toBeVisible();
    await page.screenshot({ path: `${OUT}/desktop-first-run-${scheme}.png` });
  });

  test(`library (${scheme})`, async ({ page }) => {
    await open(page, "idle", scheme);
    await page.getByRole("button", { name: "Bibliothek" }).click();
    await expect(page.getByRole("button", { name: /A001_C001\.mov/ }).first()).toBeVisible();
    await page.screenshot({ path: `${OUT}/desktop-library-${scheme}.png` });
    await page
      .getByRole("button", { name: /A001_C001\.mov/ })
      .first()
      .click();
    await expect(page.getByText("Szenen", { exact: true })).toBeVisible();
    await page.screenshot({ path: `${OUT}/desktop-library-detail-${scheme}.png` });
  });
}

test("processor page has no console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  await open(page, "idle", "light");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(errors).toEqual([]);
});
