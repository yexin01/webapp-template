/**
 * What the sandbox end-to-end run (E2E_SANDBOX=1) needs, and the words it uses when something is missing.
 * Pure functions of an environment, so the Playwright config, the setup project and the unit tests share them.
 * Messages name variables and dashboard settings, never values.
 */
export type Env = Readonly<Record<string, string | undefined>>;

/** A Clerk test address: development instances never send mail to it and accept the fixed code 424242. */
export const DEFAULT_TEST_EMAIL = "e2e+clerk_test@example.com";

/**
 * The only variables the runner takes from the root .env. Its workers need the Clerk keys to mint testing
 * tokens and to sign the test user in; the API and the web app read the rest of the file themselves, and
 * the web server inherits the runner's environment, so nothing else may be loaded into it. E2E_SANDBOX is
 * deliberately absent: it is the switch that decides whether the file is read at all.
 */
export const RUNNER_VARIABLES: readonly string[] = [
  "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
  "CLERK_SECRET_KEY",
  "E2E_CLERK_USER_EMAIL",
];

export const isSandboxRun = (env: Env): boolean => env.E2E_SANDBOX === "1";

export const testUserEmail = (env: Env): string => env.E2E_CLERK_USER_EMAIL?.trim() || DEFAULT_TEST_EMAIL;

function frontendHost(publishableKey: string): string {
  const encoded = /^pk_(?:test|live)_(.+)$/.exec(publishableKey)?.[1] ?? "";
  return Buffer.from(encoded, "base64").toString("utf8").replace(/\$$/, "");
}

const isPlaceholder = (value: string, host = "") =>
  /replace_me|not_configured/i.test(value) || host.endsWith(".invalid");

/** Why the sandbox run cannot start, one line per problem; empty when it can. */
export function sandboxProblems(env: Env): string[] {
  const problems: string[] = [];
  const publishable = env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?.trim() ?? "";
  const secret = env.CLERK_SECRET_KEY?.trim() ?? "";
  const where = "from Clerk Dashboard > API keys into the root .env";

  if (!publishable) {
    problems.push(
      `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set: paste the publishable key of a Clerk development instance ${where}`,
    );
  } else if (isPlaceholder(publishable, frontendHost(publishable))) {
    problems.push(
      `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder: paste your own key ${where}`,
    );
  } else if (publishable.startsWith("pk_live_")) {
    problems.push(
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is a production key: this run signs in and creates a test user, so it only runs against a development instance (pk_test_...)",
    );
  } else if (!/^pk_test_\S+$/.test(publishable)) {
    problems.push(
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not a Clerk development publishable key (pk_test_...)",
    );
  }

  if (!secret) {
    problems.push(
      `CLERK_SECRET_KEY is not set: paste the secret key of the same development instance ${where}`,
    );
  } else if (isPlaceholder(secret)) {
    problems.push(`CLERK_SECRET_KEY is still the .env.example placeholder: paste your own key ${where}`);
  } else if (secret.startsWith("sk_live_")) {
    problems.push(
      "CLERK_SECRET_KEY is a production key: this run creates a test user, so it only runs against a development instance (sk_test_...)",
    );
  } else if (!/^sk_test_\S+$/.test(secret)) {
    problems.push("CLERK_SECRET_KEY is not a Clerk development secret key (sk_test_...)");
  }

  const email = testUserEmail(env);
  if (!/^[^@\s]+\+clerk_test[^@\s]*@[^@\s]+\.[^@\s]+$/.test(email)) {
    problems.push(
      "E2E_CLERK_USER_EMAIL must be a Clerk test address (it contains +clerk_test, for example e2e+clerk_test@example.com): the run creates and signs in this user, so it must never be a real person's address",
    );
  }
  return problems;
}

/** The error the config throws, so Playwright stops before it builds anything. */
export function sandboxUnavailable(problems: readonly string[]): Error {
  const error = new Error(
    [
      "The sandbox end-to-end run (E2E_SANDBOX=1) cannot start:",
      ...problems.map((problem) => `  - ${problem}`),
      "Fix the root .env (docs/integrations.md#clerk) and run again. Without E2E_SANDBOX=1 only the account-free tests run.",
    ].join("\n"),
  );
  // Playwright prints a config error's stack; the stack of this one is only noise.
  error.stack = `Error: ${error.message}`;
  return error;
}

/** What a pending session means. Clerk keeps a session pending while it has an unfinished task. */
export function pendingSessionMessage(task: string | null): string {
  if (task === "choose-organization") {
    return (
      "The test user's session is pending with the task choose-organization: this Clerk instance requires every user to belong to an organization " +
      "(the default for applications created since 2025-08-22), but the template's personal workspace needs Membership optional. " +
      "In the Clerk Dashboard open Organization settings and select Membership optional (or run `npx clerk@latest enable orgs` without --force-selection), then run again."
    );
  }
  return (
    `The test user's session is pending${task ? ` with the task ${task}` : ""}: the Clerk instance asks the user for a step before the app is usable. ` +
    "Turn that requirement off for this development instance (Clerk Dashboard), or complete it for the test user, then run again."
  );
}
