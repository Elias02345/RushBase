import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { expect, test, type APIRequestContext } from "@playwright/test";

const run = promisify(execFile);
const ROOT = process.env.RUSHBASE_E2E_ROOT ?? "";
const PASSWORD = "a-long-password-1";

/** Run the real worker CLI (`rushbase …`) from the repository, isolated to the temp library. */
async function worker(args: string[], timeout = 300_000): Promise<string> {
  const { stdout, stderr } = await run("uv", ["run", "rushbase", ...args], {
    cwd: ROOT,
    timeout,
    env: {
      ...process.env,
      RUSHBASE_DATA_DIR: process.env.RUSHBASE_E2E_WORKER_DATA ?? "",
      RUSHBASE_LIBRARY_ROOT: process.env.RUSHBASE_E2E_LIBRARY ?? "",
    },
  });
  return `${stdout}${stderr}`;
}

async function assetTotal(request: APIRequestContext): Promise<number> {
  const response = await request.get("/api/v1/assets?limit=1");
  return ((await response.json()) as { total: number | null }).total ?? 0;
}

test.describe.configure({ mode: "serial" });

test("real stack: setup, worker, library, playback, processing, admin", async ({
  page,
  request,
}) => {
  await page.addInitScript(() => window.localStorage.setItem("rushbase.lang", "de"));

  // --- setup wizard against the empty server -----------------------------------------------
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup/);
  await page.getByRole("button", { name: "Weiter" }).click();
  await page.getByLabel("Benutzername").fill("admin");
  await page.getByLabel("Passwort", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Passwort wiederholen").fill(PASSWORD);
  await page.getByRole("button", { name: "Weiter" }).click();
  await page.getByRole("button", { name: "Bibliothek einrichten" }).click();
  await page.getByRole("button", { name: "Später in der Verwaltung" }).click();
  const command = (await page.getByTestId("enroll-command").innerText()).trim();
  expect(command).toMatch(/^rushbase enroll --server \S+ --token rbe_\S+ --name \S+$/);

  // --- enroll the real worker with exactly the command the UI shows -------------------------
  const args = command.split(/\s+/).slice(1);
  args[args.indexOf("--name") + 1] = "e2e-worker";
  expect(await worker(args)).toContain("enrolled as e2e-worker");

  // --- wait until the scanner has grouped the seeded input, then process it -----------------
  await expect.poll(() => assetTotal(page.request), { timeout: 90_000 }).toBeGreaterThan(5);
  let previous = -1;
  await expect
    .poll(
      async () => {
        const total = await assetTotal(page.request);
        const settled = total === previous;
        previous = total;
        return settled;
      },
      { timeout: 60_000, intervals: [4_000] },
    )
    .toBe(true);
  await worker(["process", "--exit-when-empty"], 330_000);

  // --- dashboard ----------------------------------------------------------------------------
  await expect
    .poll(
      async () => {
        const response = await page.request.get("/api/v1/status");
        const body = (await response.json()) as { assets: Record<string, number> };
        return body.assets.filed ?? 0;
      },
      { timeout: 90_000 },
    )
    .toBeGreaterThan(0);
  await page.goto("/");
  await expect(page.getByText(/[1-9]\d* Clips sind einsortiert/)).toBeVisible();

  // --- library: search finds a clip by (part of) its name -----------------------------------
  await page.getByRole("link", { name: "Bibliothek" }).click();
  const search = page.getByRole("searchbox", { name: "Material durchsuchen" });
  await search.fill("uhd");
  await expect(page).toHaveURL(/q=uhd/);
  const tiles = page.getByRole("group", { name: "Treffer" }).getByRole("button");
  await expect(tiles.first()).toBeVisible();
  await expect(page.getByText(/1 Treffer/)).toBeVisible();

  // --- asset detail: the proxy plays (Range -> 206) and the shots are listed ----------------
  await tiles.first().click();
  await expect(page).toHaveURL(/\/assets\//);
  const video = page.locator("video");
  await expect(video).toHaveAttribute("src", /\/api\/v1\/derived\//);
  const src = (await video.getAttribute("src")) ?? "";
  const ranged = await page.request.get(src, { headers: { Range: "bytes=0-1023" } });
  expect(ranged.status()).toBe(206);
  await expect(page.getByRole("list", { name: "Schlüsselbilder" }).getByRole("button").first())
    .toBeVisible();

  // --- processing page lists the worker -----------------------------------------------------
  await page.getByRole("link", { name: /Verarbeitung/ }).click();
  await page.getByRole("tab", { name: "Worker" }).click();
  await expect(page.getByText("e2e-worker")).toBeVisible();

  // --- admin creates an enrollment token ----------------------------------------------------
  await page.getByRole("link", { name: "Verwaltung" }).click();
  await page.getByRole("tab", { name: "Worker-Tokens" }).click();
  await page.getByRole("button", { name: "Token erstellen" }).click();
  await expect(page.getByRole("dialog", { name: "Token erstellt" })).toContainText(
    "rushbase enroll",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("table", { name: "Worker-Tokens" })).toContainText("Offen");
});
