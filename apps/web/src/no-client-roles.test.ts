// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * ARCH-03: the UI renders the API's decisions and never re-derives them. A role or permission read from
 * Clerk in the browser copies a server rule, and can disagree with it: Clerk's role is live, the API's is
 * a mirror that may be minutes old. Show an action from what the API returned, such as a list's `can`.
 */
const READS_A_ROLE: [string, RegExp][] = [
  ["a Clerk role name", /["'`]org:[a-z_]+(?::[a-z_]+)*["'`]/],
  ["the membership's role", /\bmembership\??\.role\b/],
  ["orgRole or orgPermissions", /\b(?:orgRole|orgPermissions)\b/],
  ["a has({ role }) or has({ permission }) check", /\bhas\(\s*\{\s*(?:role|permission)\b/],
  ["<Protect>, or <Show> gated by a role or permission", /<Protect\b|when=\{\{\s*(?:role|permission)\b/],
];

const srcDir = import.meta.dirname;

function sources(dir = srcDir): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) return sources(file);
    return /\.tsx?$/.test(entry.name) && !/\.(?:test|d)\.tsx?$/.test(entry.name) ? [file] : [];
  });
}

describe("the UI never reads a role (ARCH-03)", () => {
  it("scans the application's sources", () => {
    const files = sources().map((file) => path.relative(srcDir, file));
    expect(files).toEqual(
      expect.arrayContaining(["features/projects/projects-panel.tsx", "features/workspace/workspace.tsx"]),
    );
  });

  it("finds no role, permission, or membership read from Clerk in any source", () => {
    const found = sources().flatMap((file) =>
      readFileSync(file, "utf8")
        .split("\n")
        .flatMap((line, index) =>
          READS_A_ROLE.filter(([, pattern]) => pattern.test(line)).map(
            ([what]) => `${path.relative(srcDir, file)}:${index + 1} reads ${what}`,
          ),
        ),
    );
    expect(
      found,
      "Show an action from what the API returned, for example the list's `can`, not from a role.",
    ).toEqual([]);
  });

  it.each([
    ['membership?.role === "org:admin"', ["a Clerk role name", "the membership's role"]],
    ["const { orgRole } = useAuth();", ["orgRole or orgPermissions"]],
    [
      'if (has({ role: "org:admin" })) {}',
      ["a Clerk role name", "a has({ role }) or has({ permission }) check"],
    ],
    [
      "has({ permission: 'org:sys_billing:manage' })",
      ["a Clerk role name", "a has({ role }) or has({ permission }) check"],
    ],
    [
      '<Protect role="org:admin">',
      ["a Clerk role name", "<Protect>, or <Show> gated by a role or permission"],
    ],
    [
      "<Show when={{ role: 'org:admin' }}>",
      ["a Clerk role name", "<Protect>, or <Show> gated by a role or permission"],
    ],
  ])("recognizes %s", (code, expected) => {
    expect(READS_A_ROLE.filter(([, pattern]) => pattern.test(code)).map(([what]) => what)).toEqual(expected);
  });

  it("lets the sign-in switches and organization ids through", () => {
    for (const code of [
      '<Show when="signed-in">{children}</Show>',
      "const { organization } = useOrganization();",
      "const orgId = organization?.id ?? null;",
    ]) {
      expect(READS_A_ROLE.filter(([, pattern]) => pattern.test(code))).toEqual([]);
    }
  });
});
