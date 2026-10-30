import { createClerkClient } from "@clerk/backend";
import { clerkSetup } from "@clerk/testing/playwright";
import { test as setup } from "@playwright/test";

import { signInTestUser } from "./support/clerk-session";
import { ensureTestUser } from "./support/clerk-user";
import { testUserEmail } from "./support/sandbox";

/**
 * The first project of a sandbox run (E2E_SANDBOX=1); the browser projects depend on it, so when it fails
 * nothing else runs and the one failure says what to fix. It runs once:
 * 1. clerkSetup fetches a Clerk testing token and puts it, with the Frontend API host, in this process's
 *    environment, which Playwright passes on to the projects that depend on this one. (A function-based
 *    globalSetup does not reach the workers, per Clerk's documentation.)
 * 2. The sandbox user is created in the development instance if it is not there yet.
 * 3. That user signs in once, as every test does, so a setting that blocks the sign-in (an organization
 *    required of every user, for example) is reported here with the change to make.
 * Evidence from this project is `sandbox`: it talks to a real Clerk development instance.
 */
setup("Clerk testing token, test user, and a first sign-in", async ({ page }) => {
  const email = testUserEmail(process.env);
  await clerkSetup({ dotenv: false });
  const clerk = createClerkClient({ secretKey: process.env.CLERK_SECRET_KEY ?? "" });
  await ensureTestUser(clerk.users, email);
  await signInTestUser(page, email);
});
