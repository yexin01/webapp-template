// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * The rendering baseline (ADR-0004 §6, ARCH-12): route files in src/app are Server Components that render static
 * content and mount feature client components, and API data loads in the browser. So src/app holds no Route
 * Handler, no Server Action, and no server-side Clerk (`auth()` and the rest of @clerk/nextjs/server), and
 * `next build` prerenders every page as static. The proxy (src/proxy.ts) is outside src/app and may use Clerk's
 * server API. Fetching on the server is a deliberate change: it needs an ADR, and an edit to this test.
 */
const ROUTE_HANDLER = /^route\.[cm]?[jt]sx?$/;

const BREAKS_THE_BASELINE: [string, RegExp][] = [
  ['a Server Action (a "use server" directive)', /(?:^|[{;])\s*["']use server["']/],
  [
    "a server-side Clerk import (@clerk/nextjs/server)",
    /\b(?:from|import|require)\b\s*\(?\s*["'`]@clerk\/nextjs\/server\b/,
  ],
];

const srcDir = import.meta.dirname;
const appDir = path.join(srcDir, "app");

function files(dir = appDir): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    return entry.isDirectory() ? files(file) : [file];
  });
}

const isSource = (file: string) => /\.[cm]?[jt]sx?$/.test(file) && !/\.(?:test|d)\.[cm]?[jt]sx?$/.test(file);
const relative = (file: string) => path.relative(srcDir, file);

describe("route files stay static Server Components (ADR-0004 §6)", () => {
  it("scans the route files", () => {
    expect(files().map(relative)).toEqual(
      expect.arrayContaining(["app/layout.tsx", "app/page.tsx", "app/dashboard/page.tsx"]),
    );
  });

  it("defines no Route Handler in src/app", () => {
    const found = files()
      .filter((file) => ROUTE_HANDLER.test(path.basename(file)))
      .map(relative);
    expect(
      found,
      "Route Handlers are not part of the rendering baseline: the API is the Python service. See ADR-0004 §6.",
    ).toEqual([]);
  });

  it("has no Server Action and no server-side Clerk import in src/app", () => {
    const found = files()
      .filter(isSource)
      .flatMap((file) =>
        readFileSync(file, "utf8")
          .split("\n")
          .flatMap((line, index) =>
            BREAKS_THE_BASELINE.filter(([, pattern]) => pattern.test(line)).map(
              ([what]) => `${relative(file)}:${index + 1} has ${what}`,
            ),
          ),
      );
    expect(
      found,
      "Load data in the browser (useApi, useResource). Fetching on the server needs an ADR: see ADR-0004 §6.",
    ).toEqual([]);
  });

  it.each([
    ['"use server";', ['a Server Action (a "use server" directive)']],
    ["'use server'", ['a Server Action (a "use server" directive)']],
    ['async function save() {\n  "use server";\n}', ['a Server Action (a "use server" directive)']],
    ['async function save() { "use server"; }', ['a Server Action (a "use server" directive)']],
    ['import { auth } from "@clerk/nextjs/server";', ["a server-side Clerk import (@clerk/nextjs/server)"]],
    [
      'const { auth } = await import("@clerk/nextjs/server");',
      ["a server-side Clerk import (@clerk/nextjs/server)"],
    ],
    [
      "const clerk = require('@clerk/nextjs/server/internal');",
      ["a server-side Clerk import (@clerk/nextjs/server)"],
    ],
  ])("recognizes %s", (code, expected) => {
    const found = code
      .split("\n")
      .flatMap((line) =>
        BREAKS_THE_BASELINE.filter(([, pattern]) => pattern.test(line)).map(([what]) => what),
      );
    expect(found).toEqual(expected);
  });

  it("lets the client-side Clerk package and other strings through", () => {
    for (const code of [
      'import { SignIn } from "@clerk/nextjs";',
      'import { ClerkProvider } from "@clerk/nextjs";',
      'import { x } from "@clerk/nextjs/errors";',
      '// no "use server" here',
      'const directive = "use server";',
      '"use client";',
    ]) {
      expect(BREAKS_THE_BASELINE.filter(([, pattern]) => pattern.test(code))).toEqual([]);
    }
  });

  it("matches the route handler file names Next.js recognizes, and nothing else", () => {
    for (const name of ["route.ts", "route.tsx", "route.js", "route.jsx"]) {
      expect(name).toMatch(ROUTE_HANDLER);
    }
    for (const name of ["page.tsx", "route.test.ts", "routes.ts", "my-route.ts", "route.css"]) {
      expect(name).not.toMatch(ROUTE_HANDLER);
    }
  });
});
