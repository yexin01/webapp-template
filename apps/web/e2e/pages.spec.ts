import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/**
 * Browser page tests. Clerk's development instances complete a browser
 * handshake with the Clerk Frontend API, so these need a real Clerk
 * development publishable key in the root .env (sandbox). Run with
 * E2E_SANDBOX=1.
 */
test.skip(!process.env.E2E_SANDBOX, "Set E2E_SANDBOX=1 with Clerk development keys in .env.");

test("landing page renders with accessible structure", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("secure foundation");
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
});

test("skip link moves focus to the main content", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "keyboard focus check uses Chromium");
  await page.goto("/");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await skip.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
});

test("unknown routes show the 404 page", async ({ page }) => {
  const response = await page.goto("/definitely-not-a-page");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Go home" })).toHaveAttribute("href", "/");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(results.violations).toEqual([]);
});
