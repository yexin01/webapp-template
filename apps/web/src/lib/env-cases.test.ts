// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

import { ConfigError, loadServerEnv } from "./env.server";

/**
 * The two validators give one verdict on the variables both apps read (ENV-03). The cases are shared
 * with apps/api/tests/unit/test_config_cases.py. Each names the variables both apps must report
 * (none: both must accept the configuration). A fixture, not a runtime manifest: it catches the day
 * one validator learns a rule the other does not.
 */
type Cases = {
  base: Record<string, string>;
  cases: { name: string; env: Record<string, string | null>; names: string[] }[];
};
const document = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, "../../../../packages/contracts/config-cases.json"), "utf8"),
) as Cases;

const NAME = /[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+/g;

describe("the web app names what the API names", () => {
  it.each(document.cases)("$name", ({ env, names }) => {
    const merged = Object.fromEntries(
      Object.entries({ ...document.base, ...env }).filter(([, value]) => value !== null),
    ) as Record<string, string>;
    let named: string[] | null = null;
    try {
      loadServerEnv(merged, { serving: true });
    } catch (error) {
      if (!(error instanceof ConfigError)) throw error;
      named = error.problems.join("\n").match(NAME) ?? [];
    }
    if (names.length === 0) {
      expect(named, "accepted by the API, refused by the web app").toBeNull();
    } else {
      expect(named, "refused by the API, accepted by the web app").not.toBeNull();
      expect(named).toEqual(expect.arrayContaining(names));
    }
  });
});
