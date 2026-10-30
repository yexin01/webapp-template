import AxeBuilder from "@axe-core/playwright";
import { clerk, clerkSetup, setupClerkTestingToken } from "@clerk/testing/playwright";
import { expect, test } from "@playwright/test";

/**
 * Sandbox end-to-end tests: real Clerk development instance, real (test-mode)
 * Stripe checkout initiation. Skipped unless these are set:
 *   E2E_SANDBOX=1 (without it, the web app runs with a placeholder Clerk key),
 *   E2E_CLERK_USER_EMAIL, E2E_CLERK_USER_PASSWORD (a dedicated test user),
 *   plus the normal sandbox keys in the root .env.
 * The integrations test also needs APP_ENV=local, or the test user's Clerk ID
 * in DIAGNOSTICS_OPERATOR_IDS.
 * Results from this file are "sandbox" evidence, never "live" evidence.
 */
const email = process.env.E2E_CLERK_USER_EMAIL;
const password = process.env.E2E_CLERK_USER_PASSWORD;

test.describe("signed-in flows (sandbox)", () => {
  test.skip(
    !process.env.E2E_SANDBOX || !email || !password,
    "Set E2E_SANDBOX=1, E2E_CLERK_USER_EMAIL, and E2E_CLERK_USER_PASSWORD to run sandbox flows.",
  );

  test.beforeAll(async () => {
    await clerkSetup();
  });

  test.beforeEach(async ({ page }) => {
    await setupClerkTestingToken({ page });
    await page.goto("/");
    await clerk.signIn({
      page,
      signInParams: { strategy: "password", identifier: email ?? "", password: password ?? "" },
    });
  });

  test("dashboard: create and delete a personal project", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Personal workspace" })).toBeVisible();
    const name = `e2e ${Date.now()}`;
    await page.getByLabel("New project name").fill(name);
    await page.getByRole("button", { name: "Create" }).click();
    // The list item, not the status message that announces it.
    const item = page.getByRole("listitem").filter({ hasText: name });
    await expect(item).toBeVisible();
    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: `Delete ${name}` }).click();
    await expect(item).toHaveCount(0);
  });

  test("unknown routes show the 404 page", async ({ page }) => {
    const response = await page.goto("/definitely-not-a-page");
    expect(response?.status()).toBe(404);
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Go home" })).toHaveAttribute("href", "/");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations).toEqual([]);
  });

  test("validation errors are announced", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Create" }).click();
    // Next.js's route announcer is also role="alert", so the alert is picked by its message.
    await expect(page.getByRole("alert").filter({ hasText: "Enter a project name" })).toBeVisible();
  });

  test("free user sees the paywall on the Pro feature", async ({ page }) => {
    await page.goto("/pro");
    await expect(page.getByRole("heading", { name: "Upgrade to Pro" })).toBeVisible();
  });

  test("billing page offers monthly and yearly checkout", async ({ page }) => {
    await page.goto("/billing");
    await expect(page.getByRole("button", { name: "Pro — monthly" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pro — yearly" })).toBeVisible();
    await page.getByRole("button", { name: "Pro — monthly" }).click();
    await page.waitForURL(/checkout\.stripe\.com/);
  });

  test("checkout success redirect alone does not grant Pro", async ({ page }) => {
    await page.goto("/billing/success?session_id=cs_test_forged");
    await expect(page.getByRole("heading", { name: "Confirming your subscription" })).toBeVisible();
    await page.goto("/pro");
    await expect(page.getByRole("heading", { name: "Upgrade to Pro" })).toBeVisible();
  });

  test("integrations page runs the database check and is accessible", async ({ page }) => {
    await page.goto("/integrations");
    await expect(page.getByRole("heading", { name: "Integrations", level: 1 })).toBeVisible();
    // Exact: a role name otherwise matches as a substring, and "Clerk session" is also a card.
    await expect(page.getByRole("heading", { name: "Clerk", level: 3, exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Run check for PostgreSQL" }).click();
    const card = page
      .getByRole("listitem")
      .filter({ has: page.getByRole("heading", { name: "PostgreSQL" }) });
    await expect(card.getByText("Passed")).toBeVisible();
    await expect(card).toContainText("as app_api");
    await page.getByRole("button", { name: "Run check for Clerk" }).click();
    const clerkCard = page
      .getByRole("listitem")
      .filter({ has: page.getByRole("heading", { name: "Clerk", exact: true }) });
    await expect(clerkCard.getByText("Passed")).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  });
});
