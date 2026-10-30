/** The part of Clerk's Backend API client (`createClerkClient(...).users`) the test user needs. */
export type UsersApi = {
  getUserList(params: { emailAddress: string[]; limit: number }): Promise<{ data: { id: string }[] }>;
  createUser(params: { emailAddress: string[]; skipPasswordRequirement: boolean }): Promise<{ id: string }>;
};

/** What Clerk said, without anything that could carry a key: its error codes and messages, or the error's text. */
function describe(error: unknown): string {
  const listed = (error as { errors?: { code?: string; message?: string }[] } | null)?.errors;
  if (Array.isArray(listed) && listed.length) {
    return listed.map((entry) => [entry.code, entry.message].filter(Boolean).join(": ")).join("; ");
  }
  return error instanceof Error ? error.message : String(error);
}

/**
 * The dedicated sandbox user, created in the development instance on the first run (no password: the run signs
 * it in with a sign-in token). Idempotent. A create that fails is checked once more by listing, in case another
 * run made the user in between.
 */
export async function ensureTestUser(
  users: UsersApi,
  email: string,
): Promise<{ id: string; created: boolean }> {
  const find = async () => {
    try {
      return (await users.getUserList({ emailAddress: [email], limit: 1 })).data[0];
    } catch (error) {
      throw new Error(
        `Could not look up the Clerk test user ${email} (${describe(error)}). ` +
          "Check that CLERK_SECRET_KEY is the secret key of the same development instance as NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY.",
        { cause: error },
      );
    }
  };
  const existing = await find();
  if (existing) return { id: existing.id, created: false };
  try {
    const user = await users.createUser({ emailAddress: [email], skipPasswordRequirement: true });
    return { id: user.id, created: true };
  } catch (error) {
    const raced = await find();
    if (raced) return { id: raced.id, created: false };
    throw new Error(
      `Could not create the Clerk test user ${email} (${describe(error)}). ` +
        "Check the development instance's sign-up requirements (Clerk Dashboard > User & authentication) and its restrictions, or set E2E_CLERK_USER_EMAIL to an existing Clerk test user.",
      { cause: error },
    );
  }
}
