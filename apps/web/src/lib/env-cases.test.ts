// @vitest-environment node
import { readFileSync } from "node:fs";
import path from "node:path";

import { browserOrigin, ConfigError, loadServerEnv, type ServerEnv } from "./env.server";

/**
 * The two validators give one verdict on the variables both apps read (ENV-03). The cases are shared
 * with apps/api/tests/unit/test_config_cases.py. Each names the variables both apps must report
 * (none: both must accept the configuration), and, for a configuration both accept, may pin the
 * origins whose session tokens they trust. A fixture, not a runtime manifest: it catches the day
 * one validator learns a rule the other does not.
 */
type Cases = {
  base: Record<string, string>;
  cases: {
    name: string;
    env: Record<string, string | null>;
    names: string[];
    authorizedParties?: string[];
  }[];
  origins: Record<string, string | null>;
};
const document = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, "../../../../packages/contracts/config-cases.json"), "utf8"),
) as Cases;

const NAME = /[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+/g;

describe("the web app names what the API names", () => {
  it.each(document.cases)("$name", ({ env, names, authorizedParties }) => {
    const merged = Object.fromEntries(
      Object.entries({ ...document.base, ...env }).filter(([, value]) => value !== null),
    ) as Record<string, string>;
    let named: string[] | null = null;
    let accepted: ServerEnv | null = null;
    try {
      accepted = loadServerEnv(merged, { serving: true });
    } catch (error) {
      if (!(error instanceof ConfigError)) throw error;
      named = error.problems.join("\n").match(NAME) ?? [];
    }
    if (names.length === 0) {
      expect(named, "accepted by the API, refused by the web app").toBeNull();
      if (authorizedParties) expect(accepted?.clerkAuthorizedParties).toEqual(authorizedParties);
    } else {
      expect(named, "refused by the API, accepted by the web app").not.toBeNull();
      expect(named).toEqual(expect.arrayContaining(names));
    }
  });
});

describe("the web app reads an origin as the API does", () => {
  it.each(Object.entries(document.origins))("%j is %j", (value, origin) => {
    expect(browserOrigin(value)).toBe(origin);
  });
});
