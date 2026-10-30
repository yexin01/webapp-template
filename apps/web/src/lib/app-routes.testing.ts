/**
 * Test support for the fitness tests of default-deny page access (not a test
 * file itself): which routes src/app answers, and which of them are meant to be
 * public. page-access.test.ts and app/layout.test.tsx read the same list.
 */
import { readdirSync } from "node:fs";
import path from "node:path";

/**
 * The pages meant to be reachable without signing in. Adding to this list is a
 * deliberate second step next to PUBLIC_PAGES, so a page cannot become public by
 * a one-line slip or by being created in the wrong place.
 */
export const EXPECTED_PUBLIC_ROUTES = ["/", "/sign-in", "/sign-up"];

export const appDir = path.resolve(import.meta.dirname, "../app");
const ROUTE_FILE = /^(?:page|route)\.(?:[cm]?[jt]sx?)$/;
const LAYOUT_FILE = /^layout\.(?:[cm]?[jt]sx?)$/;

export type Route = { file: string; pattern: string; samples: string[] };

/** Every page and route handler under src/app, with the URLs it answers (sample values for dynamic segments). */
export function routes(dir = appDir, segments: string[] = []): Route[] {
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

/** Every layout under src/app, relative to it. */
export function layouts(dir = appDir): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name.startsWith("_") ? [] : layouts(full);
    return LAYOUT_FILE.test(entry.name) ? [path.relative(appDir, full)] : [];
  });
}
