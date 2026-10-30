// @vitest-environment node
import { readdirSync } from "node:fs";
import path from "node:path";

import { isPublicPage, PUBLIC_PAGES } from "./page-access";

/**
 * The pages meant to be reachable without signing in. Adding to this list is a
 * deliberate second step next to PUBLIC_PAGES, so a page cannot become public by
 * a one-line slip or by being created in the wrong place.
 */
const EXPECTED_PUBLIC_ROUTES = ["/", "/sign-in", "/sign-up"];

const appDir = path.resolve(import.meta.dirname, "../app");
const ROUTE_FILE = /^(?:page|route)\.(?:[cm]?[jt]sx?)$/;

type Route = { file: string; pattern: string; samples: string[] };

/** Every page and route handler under src/app, with the URLs it answers (sample values for dynamic segments). */
function routes(dir = appDir, segments: string[] = []): Route[] {
  const found: Route[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      const name = entry.name;
      if (name.startsWith("_")) continue; // private folder: not a route
      if (name.startsWith("@") || /^\(\.{1,3}\)/.test(name)) {
        throw new Error(`${name}: parallel and intercepting routes are not covered; extend this test first.`);
      }
      found.push(...routes(path.join(dir, name), [...segments, name]));
    } else if (ROUTE_FILE.test(entry.name)) {
      const url = segments.filter((s) => !/^\(.*\)$/.test(s));
      const optionalCatchAll = url.at(-1)?.startsWith("[[...");
      const base = url.filter((s) => !s.startsWith("[[..."));
      const sample = (s: string) => (s.startsWith("[...") ? "a/b" : s.startsWith("[") ? "a" : s);
      const concrete = base.map(sample).join("/");
      found.push({
        file: path.relative(appDir, path.join(dir, entry.name)),
        pattern: `/${base.join("/")}`,
        samples: [`/${concrete}`, ...(optionalCatchAll ? [`/${concrete}/a/b`.replace("//", "/")] : [])],
      });
    }
  }
  return found;
}

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
        `add it to PUBLIC_PAGES (src/lib/page-access.ts) and to EXPECTED_PUBLIC_ROUTES in this test.`,
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
