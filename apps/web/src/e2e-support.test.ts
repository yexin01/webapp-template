// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

import { ensureTestUser, type UsersApi } from "../e2e/support/clerk-user";
import {
  DEFAULT_TEST_EMAIL,
  isSandboxRun,
  pendingSessionMessage,
  sandboxProblems,
  sandboxUnavailable,
  testUserEmail,
} from "../e2e/support/sandbox";

/**
 * The parts of the sandbox end-to-end run that can be checked without Clerk: what it needs before it
 * starts, and how it finds or creates its test user. Whether Clerk accepts the sign-in is NOT VERIFIED
 * here (it needs a development instance: DEC-006).
 */
const exampleValue = (name: string) =>
  new RegExp(`^${name}=(.+)$`, "m").exec(
    readFileSync(path.resolve(import.meta.dirname, "../../../.env.example"), "utf8"),
  )?.[1];

// Built at run time: no secret-shaped literal in the source.
const publishable = `pk_${"test"}_${Buffer.from("example-app.clerk.accounts.dev$").toString("base64")}`;
const secret = `sk_${"test"}_${"c".repeat(30)}`;
const good = { NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: publishable, CLERK_SECRET_KEY: secret };

describe("the sandbox switch", () => {
  it("is on only for E2E_SANDBOX=1", () => {
    expect(isSandboxRun({ E2E_SANDBOX: "1" })).toBe(true);
    for (const value of [undefined, "", "0", "true", "yes", " 1"]) {
      expect(isSandboxRun({ E2E_SANDBOX: value })).toBe(false);
    }
  });

  it("signs in a Clerk test address, by default one the suite owns", () => {
    expect(testUserEmail({})).toBe(DEFAULT_TEST_EMAIL);
    expect(testUserEmail({ E2E_CLERK_USER_EMAIL: "  " })).toBe(DEFAULT_TEST_EMAIL);
    expect(testUserEmail({ E2E_CLERK_USER_EMAIL: " me+clerk_test@example.org " })).toBe(
      "me+clerk_test@example.org",
    );
    expect(DEFAULT_TEST_EMAIL).toContain("+clerk_test");
  });
});

describe("what must be in place before a sandbox run starts", () => {
  it("accepts development keys", () => {
    expect(sandboxProblems(good)).toEqual([]);
  });

  it("names each missing key", () => {
    const problems = sandboxProblems({});
    expect(problems).toHaveLength(2);
    expect(problems[0]).toMatch(
      /^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set: .*Clerk Dashboard > API keys/,
    );
    expect(problems[1]).toMatch(/^CLERK_SECRET_KEY is not set: /);
  });

  it("refuses the placeholders .env.example ships, and the older ones", () => {
    const shipped = {
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: exampleValue("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"),
      CLERK_SECRET_KEY: exampleValue("CLERK_SECRET_KEY"),
    };
    expect(sandboxProblems(shipped).map((text) => text.split(":")[0])).toEqual([
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is still the .env.example placeholder",
      "CLERK_SECRET_KEY is still the .env.example placeholder",
    ]);
    const older = {
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_replace_me",
      CLERK_SECRET_KEY: "sk_test_replace_me",
    };
    expect(sandboxProblems(older)).toHaveLength(2);
    expect(sandboxProblems(older).join()).toContain("still the .env.example placeholder");
  });

  it("refuses production keys: the run creates users and signs in", () => {
    const live = {
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: `pk_${"live"}_${Buffer.from("clerk.example.com$").toString("base64")}`,
      CLERK_SECRET_KEY: `sk_${"live"}_${"d".repeat(30)}`,
    };
    const problems = sandboxProblems(live);
    expect(problems).toHaveLength(2);
    expect(problems.every((text) => text.includes("production key"))).toBe(true);
    expect(problems.join()).not.toContain("d".repeat(30));
  });

  it("refuses values that are not Clerk keys", () => {
    const problems = sandboxProblems({ NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_x", CLERK_SECRET_KEY: "sk_x" });
    expect(problems).toEqual([
      "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not a Clerk development publishable key (pk_test_...)",
      "CLERK_SECRET_KEY is not a Clerk development secret key (sk_test_...)",
    ]);
  });

  it("allows only a Clerk test address for the test user", () => {
    expect(sandboxProblems({ ...good, E2E_CLERK_USER_EMAIL: "qa+clerk_test@example.com" })).toEqual([]);
    const problems = sandboxProblems({ ...good, E2E_CLERK_USER_EMAIL: "real.person@example.com" });
    expect(problems).toHaveLength(1);
    expect(problems[0]).toMatch(
      /^E2E_CLERK_USER_EMAIL must be a Clerk test address \(it contains \+clerk_test/,
    );
  });

  it("says it in one error that lists every problem and nothing else, with no stack to print", () => {
    const error = sandboxUnavailable(["A is not set", "B is wrong"]);
    expect(error.message).toBe(
      [
        "The sandbox end-to-end run (E2E_SANDBOX=1) cannot start:",
        "  - A is not set",
        "  - B is wrong",
        "Fix the root .env (docs/integrations.md#clerk) and run again. Without E2E_SANDBOX=1 only the account-free tests run.",
      ].join("\n"),
    );
    expect(error.stack).toBe(`Error: ${error.message}`);
  });
});

describe("a pending session", () => {
  it("names the Clerk setting when every user must belong to an organization", () => {
    const text = pendingSessionMessage("choose-organization");
    expect(text).toContain("Membership optional");
    expect(text).toContain("Organization settings");
    expect(text).toContain("npx clerk@latest enable orgs");
  });

  it("names any other task, or none", () => {
    expect(pendingSessionMessage("setup-mfa")).toContain("with the task setup-mfa");
    expect(pendingSessionMessage(null)).not.toContain("with the task");
  });
});

describe("the sandbox user", () => {
  const found = { data: [{ id: "user_existing" }] };
  const none = { data: [] as { id: string }[] };

  it("is used as it is when it exists", async () => {
    const users: UsersApi = { getUserList: vi.fn(async () => found), createUser: vi.fn() };
    expect(await ensureTestUser(users, "e2e+clerk_test@example.com")).toEqual({
      id: "user_existing",
      created: false,
    });
    expect(users.getUserList).toHaveBeenCalledWith({
      emailAddress: ["e2e+clerk_test@example.com"],
      limit: 1,
    });
    expect(users.createUser).not.toHaveBeenCalled();
  });

  it("is created without a password when it does not exist", async () => {
    const users: UsersApi = {
      getUserList: vi.fn(async () => none),
      createUser: vi.fn(async () => ({ id: "user_new" })),
    };
    expect(await ensureTestUser(users, "e2e+clerk_test@example.com")).toEqual({
      id: "user_new",
      created: true,
    });
    expect(users.createUser).toHaveBeenCalledWith({
      emailAddress: ["e2e+clerk_test@example.com"],
      skipPasswordRequirement: true,
    });
  });

  it("is found again when another run created it between the list and the create", async () => {
    const getUserList = vi.fn().mockResolvedValueOnce(none).mockResolvedValueOnce(found);
    const createUser = vi.fn().mockRejectedValue(
      Object.assign(new Error("Unprocessable"), {
        errors: [{ code: "form_identifier_exists", message: "taken" }],
      }),
    );
    expect(await ensureTestUser({ getUserList, createUser }, "a+clerk_test@example.com")).toEqual({
      id: "user_existing",
      created: false,
    });
  });

  it("fails with Clerk's own error codes when it cannot be created", async () => {
    const getUserList = vi.fn(async () => none);
    const createUser = vi.fn().mockRejectedValue(
      Object.assign(new Error("Unprocessable Entity"), {
        errors: [{ code: "form_param_missing", message: "phone_number is required" }],
      }),
    );
    const error = await ensureTestUser({ getUserList, createUser }, "a+clerk_test@example.com").catch(
      (reason: Error) => reason,
    );
    expect((error as Error).message).toMatch(
      /^Could not create the Clerk test user a\+clerk_test@example\.com \(form_param_missing: phone_number is required\)\. Check the development instance's sign-up requirements/,
    );
    expect(getUserList).toHaveBeenCalledTimes(2);
  });

  it("points at the secret key when Clerk will not even list users", async () => {
    const getUserList = vi.fn().mockRejectedValue(
      Object.assign(new Error("Unauthorized"), {
        errors: [{ code: "authentication_invalid", message: "Invalid authentication" }],
      }),
    );
    const error = await ensureTestUser(
      { getUserList, createUser: vi.fn() },
      "a+clerk_test@example.com",
    ).catch((reason: Error) => reason);
    expect((error as Error).message).toMatch(
      /authentication_invalid: Invalid authentication\)\. Check that CLERK_SECRET_KEY is the secret key of the same development instance/,
    );
  });
});
