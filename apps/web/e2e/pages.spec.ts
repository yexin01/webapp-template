import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { ACCOUNT_FREE, expect, test } from "./support/browser";

/**
 * Browser tests of the pages a signed-out visitor can open, and of the proxy's default deny. They run in
 * both tiers described in support/browser.ts: account-free by default (a placeholder Clerk key, clerk-js
 * blocked, mocked evidence), and against a real Clerk development instance with E2E_SANDBOX=1.
 */
const WCAG = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

/** Axe findings as short lines. `exclude` leaves out markup that is not the app's, such as Clerk's sign-in form. */
async function accessibilityFindings(page: Page, exclude?: string): Promise<string[]> {
  const builder = new AxeBuilder({ page }).withTags(WCAG);
  if (exclude) builder.exclude(exclude);
  return (await builder.analyze()).violations.map((violation) => `${violation.id}: ${violation.help}`);
}

test("landing page renders with accessible structure", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("secure foundation");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  expect(await accessibilityFindings(page)).toEqual([]);
});

for (const path of ["/sign-in", "/sign-up"]) {
  test(`${path} opens without signing in and keeps the app's layout accessible`, async ({ page }) => {
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
    await expect(page.getByRole("main")).toBeAttached();
    expect(await accessibilityFindings(page, ".auth-page")).toEqual([]);
  });
}

test("keyboard: the skip link comes first and moves focus to the main content", async ({ page }) => {
  await page.goto("/");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await page.keyboard.press("Tab");
  await expect(skip).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("nav a.brand")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(skip).toBeFocused();
  await skip.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
});

// The proxy does not run on a path that looks like a file, so this is how a signed-out visitor meets the 404 page.
test("a missing file shows the 404 page", async ({ page }) => {
  const response = await page.goto("/missing.png");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Page not found", level: 1 })).toBeVisible();
  await expect(page.getByRole("link", { name: "Go home" })).toHaveAttribute("href", "/");
  expect(await accessibilityFindings(page)).toEqual([]);
});

// Default deny (src/lib/page-access.ts): only listed pages are public, and the proxy cannot tell a page from a
// typo. The 404 page itself is shown to signed-in users (authenticated.spec.ts).
for (const path of ["/dashboard", "/definitely-not-a-page"]) {
  test(`${path} asks a signed-out visitor to sign in and returns them afterwards`, async ({ page }) => {
    await page.goto(path);
    await expect(page).toHaveURL(/\/sign-in\?redirect_url=/);
    const after = new URL(page.url()).searchParams.get("redirect_url") ?? "";
    expect(new URL(after, page.url()).pathname).toBe(path);
  });
}

// The only check that the app's own JavaScript runs under the real Content Security Policy: the notice is
// drawn by a client component after hydration. Fake time moves only when asked, so the SDK's own timeouts
// and the app's backstop pass at once instead of after half a minute.
test("sign-in unavailable notice and Reload appear when clerk-js cannot load", async ({ page }) => {
  test.skip(!ACCOUNT_FREE, "clerk-js loads in sandbox runs.");
  await page.clock.install();
  await page.goto("/");
  const notice = page.getByRole("status").filter({ hasText: "Sign-in is unavailable." });
  await expect(async () => {
    await page.clock.runFor(10_000);
    await expect(notice).toBeVisible({ timeout: 500 });
  }).toPass({ timeout: 30_000 });
  const reload = page.getByRole("button", { name: "Reload" });
  await expect(reload).toBeVisible();
  await Promise.all([page.waitForEvent("load"), reload.click()]);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("secure foundation");
});
