// @vitest-environment node
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

/**
 * Clerk's <Show> resolves to a server component wherever it is imported from
 * a server module, and that one calls auth(): on a path the proxy skips
 * (/favicon.ico, /missing.png, /api/v1x) the layout then fails with a 500
 * instead of a 404. Server modules use SignedInOnly / SignedOutOnly instead;
 * e2e/http.spec.ts checks the responses themselves.
 */
const src = path.resolve(import.meta.dirname, "..");

function modules(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return modules(full);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

const isClientModule = (code: string) => /^\s*(?:\/\/[^\n]*\n\s*)*["']use client["']/.test(code);
const importsClerkShow = (code: string) =>
  /import\s*\{[^}]*\bShow\b[^}]*\}\s*from\s*["']@clerk\/nextjs["']/.test(code);

describe("signed-in state in server components", () => {
  it("never uses Clerk's server <Show> outside a client module", () => {
    const offenders = modules(src)
      .filter((file) => {
        const code = readFileSync(file, "utf8");
        return importsClerkShow(code) && !isClientModule(code);
      })
      .map((file) => path.relative(src, file));
    expect(offenders).toEqual([]);
  });

  it("detects the pattern it guards against", () => {
    const server = 'import { Show, SignUpButton } from "@clerk/nextjs";\nexport default function P() {}';
    expect(importsClerkShow(server) && !isClientModule(server)).toBe(true);
    expect(isClientModule(`"use client";\n${server}`)).toBe(true);
  });
});
