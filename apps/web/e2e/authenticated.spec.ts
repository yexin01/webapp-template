import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { signInTestUser } from "./support/clerk-session";
import { isSandboxRun, testUserEmail } from "./support/sandbox";

/**
 * Sandbox end-to-end tests: a real Clerk development instance and real (test-mode) Stripe checkout
 * initiation. They run only with E2E_SANDBOX=1 (on the command line, never from .env) and the Clerk
 * development keys in the root .env; playwright.config.ts refuses to start without them, naming what is
 * missing, and the clerk-setup project (clerk.setup.ts) creates the test user and proves one sign-in
 * before any of these runs. Every test signs that user in with a sign-in token (support/clerk-session.ts).
 * The integrations test also needs the API to let the user run diagnostics: APP_ENV=local (the .env
 * default) or the user's Clerk ID in DIAGNOSTICS_OPERATOR_IDS.
 * Results from this file are "sandbox" evidence, never "live" evidence.
 */
const email = testUserEmail(process.env);

/** The status the API gives this signed-in user for a GET, asked the way the pages ask. */
async function apiStatus(page: Page, path: string): Promise<number> {
  return page.evaluate(async (target) => {
    const token = await window.Clerk.session?.getToken();
    return (await fetch(target, { headers: { Authorization: `Bearer ${token}` } })).status;
  }, path);
}

test.describe("signed-in flows (sandbox)", () => {
  test.skip(
    !isSandboxRun(process.env),
    "Set E2E_SANDBOX=1, with the Clerk development keys in the root .env, to run the signed-in flows.",
  );

  test.beforeEach(async ({ page }) => {
    await signInTestUser(page, email);
  });

  test("dashboard: create and delete a personal project", async ({ page }, testInfo) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Personal workspace" })).toBeVisible();
    const name = `e2e ${testInfo.project.name} ${Date.now()}`;
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
    await page.goto("/");
    expect(
      await apiStatus(page, "/api/v1/diagnostics/integrations"),
      "diagnostics are not open to the test user: run the API with APP_ENV=local (the .env default), or list the test user's Clerk ID in DIAGNOSTICS_OPERATOR_IDS",
    ).toBe(200);
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
