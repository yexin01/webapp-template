import { clerk, setupClerkTestingToken } from "@clerk/testing/playwright";
import type { Page } from "@playwright/test";

import { pendingSessionMessage } from "./sandbox";

type ClerkWindow = {
  Clerk?: {
    session?: { status?: string } | null;
    client?: { sessions?: { status?: string; currentTask?: { key?: string } | null }[] };
  };
};

/** The task of a session Clerk is holding back, or undefined when no session is pending. */
async function pendingTask(page: Page): Promise<string | null | undefined> {
  const sessions = await page
    .evaluate(() =>
      ((window as unknown as ClerkWindow).Clerk?.client?.sessions ?? []).map((session) => ({
        status: session.status ?? null,
        task: session.currentTask?.key ?? null,
      })),
    )
    .catch(() => []);
  return sessions.find((session) => session.status === "pending")?.task;
}

/**
 * Signs the sandbox user in with a sign-in token (a "ticket"): no password, and no new-device step, which
 * Clerk applies to password sign-ins only (Device Trust, status needs_client_trust). It ends only with an
 * active session. A pending one (the instance's task for the user, usually choose-organization) fails here
 * with the dashboard setting to change, instead of every test timing out on a page that never shows the user.
 */
export async function signInTestUser(page: Page, emailAddress: string): Promise<void> {
  await setupClerkTestingToken({ page });
  await page.goto("/");
  try {
    await clerk.signIn({ page, emailAddress });
    await page.waitForFunction(
      () => (window as unknown as ClerkWindow).Clerk?.session?.status === "active",
      undefined,
      { timeout: 15_000 },
    );
  } catch (error) {
    const task = await pendingTask(page);
    if (task !== undefined) throw new Error(pendingSessionMessage(task), { cause: error });
    throw new Error(
      `Signing in the Clerk test user failed: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    );
  }
}
