// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

import { appDir, EXPECTED_PUBLIC_ROUTES, layouts, routes } from "./app-routes.testing";
import { isPublicPage, PUBLIC_PAGES, signInUrlFor } from "./page-access";

describe("page access (default deny)", () => {
  const all = routes();

  it("finds the application's pages", () => {
    expect(all.map((r) => r.pattern)).toEqual(expect.arrayContaining(["/", "/dashboard", "/sign-in"]));
  });

  it("makes exactly the intended pages public, and every other page requires sign-in", () => {
    const publicRoutes = all.filter((r) => r.samples.some(isPublicPage));
    expect(
      publicRoutes.map((r) => r.pattern).sort(),
      `A page is public that should not be. New pages require sign-in; to make one public on purpose, ` +
        `add it to PUBLIC_PAGES (src/lib/page-access.ts) and to EXPECTED_PUBLIC_ROUTES (src/lib/app-routes.testing.ts).`,
    ).toEqual(EXPECTED_PUBLIC_ROUTES);
    // A route is either public in every URL it answers or in none (no half-open catch-all).
    for (const route of all) {
      expect(new Set(route.samples.map(isPublicPage)).size, route.file).toBe(1);
    }
  });

  it("lists no public entry that no page uses", () => {
    const patterns = new Set(all.map((r) => r.pattern));
    for (const entry of PUBLIC_PAGES) {
      expect(patterns.has(entry.replace(/\/\*$/, "") || "/"), `${entry} matches no page`).toBe(true);
    }
  });

  it("denies a page that is created without being listed", () => {
    expect(isPublicPage("/notes")).toBe(false);
    expect(isPublicPage("/notes/1")).toBe(false);
    expect(isPublicPage("/(app)/notes")).toBe(false);
  });
});

describe("the page gate covers every page", () => {
  const source = (file: string) => readFileSync(path.join(appDir, file), "utf8");

  it("has one root layout: no route group starts a second document that the gate would not wrap", () => {
    const documents = layouts().filter((file) => /<html[\s>]/.test(source(file)));
    expect(documents).toEqual(["layout.tsx"]);
  });

  it("wraps whatever a page renders in the page gate, which the allowlist above decides", () => {
    expect(source("layout.tsx")).toMatch(/<PageGate>\s*\{children\}\s*<\/PageGate>/);
    expect(readFileSync(path.join(import.meta.dirname, "../ui/page-gate.tsx"), "utf8")).toMatch(
      /import \{[^}]*\bisPublicPage\b[^}]*\} from "@\/lib\/page-access"/,
    );
  });
});

describe("signInUrlFor (SEC-14)", () => {
  const ORIGIN = "https://app.example.com";

  it.each([
    ["/dashboard", "", "/dashboard"],
    ["/", "", "/"],
    ["/billing", "?plan=pro&from=email", "/billing?plan=pro&from=email"],
    ["/billing/success", "?session=abc%2F123", "/billing/success?session=abc%2F123"],
    // Whatever names another origin, or is not a path, becomes the home page.
    ["//evil.example.com/dashboard", "", "/"],
    ["//evil.example.com", "?a=1", "/"],
    ["/\\evil.example.com", "", "/"],
    ["/a\\b", "", "/"],
    ["https://evil.example.com/", "", "/"],
    ["javascript:alert(1)", "", "/"],
    ["evil.example.com", "", "/"],
    ["", "", "/"],
    ["/ok\n/evil", "", "/"],
    ["/ok\t/evil", "", "/"],
    // A query string that cannot be sent as it is gives way to the path.
    ["/ok", "?x=\r\ny", "/ok"],
    ["/ok", `?${"x".repeat(2000)}`, "/ok"],
    [`/${"p".repeat(2000)}`, "?a=1", "/"],
  ])("sends %j with %j back to %j", (pathname, search, returnTo) => {
    const url = signInUrlFor(pathname, search);
    expect(url).toBe(`/sign-in?redirect_url=${encodeURIComponent(returnTo)}`);
    // Whatever came in, the browser lands on this origin after signing in.
    const target = new URL(url, ORIGIN).searchParams.get("redirect_url") ?? "";
    expect(new URL(target, ORIGIN).origin).toBe(ORIGIN);
  });
});

describe("isPublicPage", () => {
  it.each(["/", "/sign-in", "/sign-in/", "/sign-in/factor-one", "/sign-up", "/sign-up/verify-email-address"])(
    "allows %s",
    (pathname) => expect(isPublicPage(pathname)).toBe(true),
  );

  it.each([
    "/dashboard",
    "/billing",
    "/billing/success",
    "/pro",
    "/integrations",
    "/sign-inx",
    "/sign-in-extra/x",
    "/x/sign-in",
    "/Sign-In",
    "//",
    "//sign-in",
    "/sign-in%2F..%2Fdashboard/x",
    "/dashboard/sign-in",
    "",
  ])("denies %s", (pathname) => expect(isPublicPage(pathname)).toBe(false));
});
