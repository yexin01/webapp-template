import { clerk, clerkSetup, setupClerkTestingToken } from "@clerk/testing/playwright";
import { expect, test } from "@playwright/test";

/**
 * Sandbox end-to-end tests: real Clerk development instance, real (test-mode)
 * Stripe checkout initiation. Skipped unless these are set:
 *   E2E_CLERK_USER_EMAIL, E2E_CLERK_USER_PASSWORD (a dedicated test user)
 *   plus the normal sandbox keys in the root .env.
 * Results from this file are "sandbox" evidence, never "live" evidence.
 */
const email = process.env.E2E_CLERK_USER_EMAIL;
const password = process.env.E2E_CLERK_USER_PASSWORD;

test.describe("signed-in flows (sandbox)", () => {
  test.skip(
    !email || !password,
    "Set E2E_CLERK_USER_EMAIL and E2E_CLERK_USER_PASSWORD to run sandbox flows.",
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
    await expect(page.getByText(name)).toBeVisible();
    page.once("dialog", (dialog) => void dialog.accept());
    await page.getByRole("button", { name: `Delete ${name}` }).click();
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test("validation errors are announced", async ({ page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByRole("alert")).toContainText("Enter a project name");
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
});
