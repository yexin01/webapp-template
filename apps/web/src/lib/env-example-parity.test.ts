// @vitest-environment node
import { generateKeyPairSync } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";

import { PHASE_PRODUCTION_BUILD } from "next/constants";

import { SERVER_VARIABLES } from "./env.server";

/**
 * `.env.example` names the variables the web app reads, tagged for it (ENV-06). The web half of a check
 * whose API half is apps/api/tests/unit/test_env_example.py (which also holds the tag vocabulary); both read
 * the file by the rules its legend gives: a variable is a line `NAME=` or `# NAME=`, and its tag is the `# >`
 * line in the comment block just above it, else the default in the title of its section.
 *
 * `problems` is a pure function of the file's text and of what the code does, so the last tests show what
 * each kind of drift reports. A failure names the variable and what to change.
 */
vi.mock("@sentry/nextjs/config", () => ({ withSentryConfig: (config: unknown) => config }));
vi.mock("./env-files", () => ({ loadEnvFiles: () => [], strayEnvFiles: () => [] }));

const root = path.resolve(import.meta.dirname, "../../../..");
const example = readFileSync(path.join(root, ".env.example"), "utf8");

/** Vercel sets it; the header lists it instead of a line. */
const PLATFORM = ["VERCEL_ENV"];
/** Read from the build environment by next.config.ts and Sentry's build plugin, never loaded from .env. */
const BUILD_ENVIRONMENT = ["SENTRY_AUTH_TOKEN", "SENTRY_ORG", "SENTRY_PROJECT"];
/** Read by test tooling, not by the app: no tag line. Each has the file that reads it, which must still name it, and why. */
const TOOLING: Record<string, { file: string; why: string }> = {
  E2E_CLERK_USER_EMAIL: {
    file: "apps/web/e2e/support/sandbox.ts",
    why: "the test user of the sandbox end-to-end run",
  },
  E2E_DATABASE_URL: {
    file: "apps/web/playwright.config.ts",
    why: "the database of the account-free end-to-end API server",
  },
};

type Tag = { apps?: string; kind?: string; baked: boolean };
type Entry = { name: string; line: number; tag: Tag | null };

const VARIABLE = /^(?:# )?([A-Z][A-Z0-9_]*)=/;
const TAG_LINE = /^# > (.+)$/;
const SECTION_DEFAULT = /\((apps=[^,)]*)/;

function parseTag(text: string): Tag {
  const tag: Tag = { baked: false };
  for (const token of text.split(/\s+/)) {
    if (token === "baked") tag.baked = true;
    else if (token.startsWith("apps=")) tag.apps = token.slice("apps=".length);
    else if (token.startsWith("kind=")) tag.kind = token.slice("kind=".length);
  }
  return tag;
}

function parseEntries(text: string): Map<string, Entry> {
  const lines = text.split("\n");
  const entries = new Map<string, Entry>();
  let sectionDefault: Tag | null = null;
  let afterSeparator = false;
  lines.forEach((line, index) => {
    const isTitle = afterSeparator && line.startsWith("# ");
    afterSeparator = line.startsWith("# =====");
    if (isTitle) {
      const found = SECTION_DEFAULT.exec(line);
      sectionDefault = found?.[1] ? parseTag(found[1]) : null;
    }
    const name = VARIABLE.exec(line)?.[1];
    if (!name) return;
    let tag = sectionDefault;
    for (let above = index - 1; above >= 0; above--) {
      const previous = lines[above] ?? "";
      if (!previous.startsWith("# ") || previous.startsWith("# =====") || VARIABLE.test(previous)) break;
      const tagged = TAG_LINE.exec(previous)?.[1];
      if (tagged) {
        tag = parseTag(tagged);
        break;
      }
    }
    if (!entries.has(name)) entries.set(name, { name, line: index + 1, tag });
  });
  return entries;
}

/** The names the header lists on the indented lines under the line that starts with `lead`. */
function listedUnder(text: string, lead: string): string[] {
  const lines = text.split("\n");
  const start = lines.findIndex((line) => line.startsWith(lead));
  if (start === -1) return [];
  const names: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (!line.startsWith("#   ")) break;
    names.push(...(line.match(/[A-Z][A-Z0-9_]+/g) ?? []));
  }
  return names;
}

/** What the code does: the names the web app reads, and what changes what next.config.ts returns. */
type Facts = {
  server: string[];
  /** NEXT_PUBLIC_* names in the source and next.config.ts that are not derived by next.config.ts. */
  public: string[];
  /** The keys of next.config.ts's `env` block. */
  derived: string[];
  /** Per server variable: does changing it change what next.config.ts returns (what the build fixes)? */
  bakedByConfig: Record<string, boolean>;
};

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
    const file = path.join(dir, item.name);
    if (item.isDirectory()) return sourceFiles(file);
    return /\.tsx?$/.test(item.name) && !/\.(test|testing)\.tsx?$/.test(item.name) ? [file] : [];
  });
}

function nextPublicNames(): string[] {
  const files = [...sourceFiles(path.join(root, "apps/web/src")), path.join(root, "apps/web/next.config.ts")];
  const names = files.flatMap(
    (file) => readFileSync(file, "utf8").match(/\bNEXT_PUBLIC_[A-Z][A-Z0-9_]*/g) ?? [],
  );
  return [...new Set(names)].sort();
}

const publicKey = generateKeyPairSync("rsa", { modulusLength: 2048 })
  .publicKey.export({ type: "spki", format: "pem" })
  .toString();
const BASE: Record<string, string> = {
  APP_ENV: "local",
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: `pk_test_${Buffer.from("clerk.example.com$").toString("base64")}`,
  CLERK_SECRET_KEY: `sk_test_${"a".repeat(24)}`,
  NEXT_PUBLIC_POSTHOG_KEY: `phc_${"a".repeat(24)}`,
  NEXT_PUBLIC_SENTRY_DSN: "https://publickey@o1.ingest.sentry.io/2",
};
/** For each variable the web app loads from the root .env: an environment with only that variable changed. */
const CHANGED: Record<string, Record<string, string>> = {
  APP_ENV: { APP_ENV: "test" },
  APP_BASE_URL: { APP_BASE_URL: "http://localhost:3001" },
  CLERK_AUTHORIZED_PARTIES: { CLERK_AUTHORIZED_PARTIES: "http://localhost:3000,http://localhost:3001" },
  API_ORIGIN: { API_ORIGIN: "http://127.0.0.1:9000" },
  ANALYTICS_MODE: { ANALYTICS_MODE: "sandbox" },
  ERROR_REPORTING_MODE: { ERROR_REPORTING_MODE: "sandbox" },
  SENTRY_TRACES_SAMPLE_RATE: { SENTRY_TRACES_SAMPLE_RATE: "0.5" },
  CLERK_SECRET_KEY: { CLERK_SECRET_KEY: `sk_test_${"b".repeat(24)}` },
  CLERK_JWT_KEY: { CLERK_JWT_KEY: publicKey },
  STRIPE_CUSTOM_DOMAIN: { STRIPE_CUSTOM_DOMAIN: "payments.example.com" },
};

/** next.config.ts under a clean environment: what `next build` fixes into the bundle, the rewrite and the CSP. */
async function configFor(env: Record<string, string>) {
  for (const name of [
    ...SERVER_VARIABLES,
    ...nextPublicNames(),
    ...BUILD_ENVIRONMENT,
    "VERCEL",
    "VERCEL_URL",
    "VERCEL_BRANCH_URL",
  ]) {
    vi.stubEnv(name, undefined);
  }
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
  const { default: nextConfig } = await import("../../next.config");
  const config = nextConfig(PHASE_PRODUCTION_BUILD);
  return { env: config.env ?? {}, rewrites: await config.rewrites?.(), headers: await config.headers?.() };
}

afterEach(() => vi.unstubAllEnvs());

async function gatherFacts(): Promise<Facts> {
  const base = await configFor(BASE);
  const baked: Record<string, boolean> = {};
  for (const name of SERVER_VARIABLES) {
    const changed = CHANGED[name];
    if (changed)
      baked[name] = JSON.stringify(await configFor({ ...BASE, ...changed })) !== JSON.stringify(base);
  }
  const derived = Object.keys(base.env);
  return {
    server: [...SERVER_VARIABLES],
    public: nextPublicNames().filter((name) => !derived.includes(name)),
    derived,
    bakedByConfig: baked,
  };
}

function problems(text: string, facts: Facts): string[] {
  const entries = parseEntries(text);
  const found: string[] = [];
  const platform = listedUnder(text, "# Set by the platform");
  const derived = listedUnder(text, "# Derived by the web build");

  for (const name of facts.derived) {
    if (!derived.includes(name)) {
      found.push(`${name}: next.config.ts sets it. List it in the header under '# Derived by the web build'`);
    }
    if (entries.has(name))
      found.push(`${name}: next.config.ts derives it, so a value set by hand is overridden. Remove the line`);
  }
  for (const name of derived) {
    if (!facts.derived.includes(name))
      found.push(`${name}: the header lists it as derived, but next.config.ts does not set it`);
  }

  const read = [
    ...facts.server.filter((name) => !PLATFORM.includes(name)),
    ...facts.public,
    ...BUILD_ENVIRONMENT,
  ];
  for (const name of PLATFORM) {
    if (facts.server.includes(name) && !platform.includes(name)) {
      found.push(
        `${name}: Vercel sets it and the web app reads it. List it in the header under '# Set by the platform'`,
      );
    }
  }
  for (const name of read) {
    const tag = entries.get(name)?.tag;
    if (!entries.has(name)) {
      found.push(
        `${name}: the web app reads it, but the file has no line for it. Add '# > apps=web ...' and '${name}=' in the section that fits`,
      );
    } else if (!tag) {
      found.push(`${name}: no tag line. Put '# > apps=... kind=... need=... scopes=...' just above it`);
    } else if (tag.apps !== "web" && tag.apps !== "both") {
      found.push(
        `${name}: tagged apps=${tag.apps}, but the web app reads it. Use apps=both, or apps=web if the API does not read it`,
      );
    }
  }
  for (const [name, entry] of entries) {
    if (name in TOOLING || !entry.tag) continue;
    const { apps, kind, baked } = entry.tag;
    if ((apps === "web" || apps === "both") && !read.includes(name)) {
      found.push(
        `${name}: tagged apps=${apps}, but the web app reads no variable of that name. Remove the line, or fix the tag`,
      );
    }
    if (name.startsWith("NEXT_PUBLIC_")) {
      if (kind !== "public") found.push(`${name}: the browser bundle receives it. Tag it kind=public`);
      if (!baked) found.push(`${name}: Next.js inlines it when the web app is built. Tag it baked`);
    } else if (kind === "public") {
      found.push(`${name}: kind=public is for NEXT_PUBLIC_* variables, which the browser bundle receives`);
    }
    const fixedByConfig = facts.bakedByConfig[name];
    if (fixedByConfig !== undefined && fixedByConfig !== baked) {
      found.push(
        fixedByConfig
          ? `${name}: changing it changes what next.config.ts returns, so the build fixes it. Tag it baked`
          : `${name}: changing it changes nothing next.config.ts returns, so a restart is enough. Drop 'baked'`,
      );
    }
  }
  for (const name of facts.server) {
    if (!PLATFORM.includes(name) && !(name in facts.bakedByConfig)) {
      found.push(
        `${name}: add an environment that changes only it to CHANGED in this test, to tell whether the build fixes it`,
      );
    }
  }
  return found;
}

describe(".env.example and the web app's variables", () => {
  it("agree", async () => {
    const found = problems(example, await gatherFacts());
    expect(found, `\n${found.join("\n")}`).toEqual([]);
  });

  it.each(Object.entries(TOOLING))("%s is still read where this test says", (name, { file }) => {
    expect(
      readFileSync(path.join(root, file), "utf8"),
      `${file} no longer reads ${name}: drop it from TOOLING`,
    ).toContain(name);
  });

  it("starts from the names the web app reads", async () => {
    const facts = await gatherFacts();
    expect(facts.server).toContain("API_ORIGIN");
    expect(facts.public).toEqual(
      expect.arrayContaining([
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
        "NEXT_PUBLIC_POSTHOG_KEY",
        "NEXT_PUBLIC_SENTRY_DSN",
      ]),
    );
    expect(facts.derived).toContain("NEXT_PUBLIC_CLERK_AUTHORIZED_PARTIES");
    expect(facts.bakedByConfig.API_ORIGIN).toBe(true);
    expect(facts.bakedByConfig.CLERK_SECRET_KEY).toBe(false);
    for (const name of BUILD_ENVIRONMENT) {
      expect(readFileSync(path.join(root, "apps/web/src/lib/env.server.ts"), "utf8")).toContain(name);
    }
  });
});

const without = (name: string) => (text: string) =>
  text.replace(new RegExp(`(?:# > [^\\n]*\\n)?(?:# )?${name}=[^\\n]*\\n`), "");
const replaced = (old: string, replacement: string) => (text: string) => {
  expect(text, `${old} is not in .env.example any more: update this test`).toContain(old);
  return text.replace(old, replacement);
};

const API_ORIGIN = "# > apps=web kind=config baked need=optional scopes=not-on-vercel\nAPI_ORIGIN=";
const DRIFT: [string, (text: string) => string, string][] = [
  ["a server variable missing", without("API_ORIGIN"), "API_ORIGIN: the web app reads it"],
  [
    "a public variable missing",
    without("NEXT_PUBLIC_SENTRY_DSN"),
    "NEXT_PUBLIC_SENTRY_DSN: the web app reads it",
  ],
  ["a build variable missing", without("SENTRY_ORG"), "SENTRY_ORG: the web app reads it"],
  [
    "a tag naming the wrong app",
    replaced(API_ORIGIN, API_ORIGIN.replace("apps=web", "apps=api")),
    "API_ORIGIN: tagged apps=api, but the web app reads it",
  ],
  [
    "a tag line missing",
    replaced("# > apps=web kind=config baked need=optional scopes=not-on-vercel\nAPI_ORIGIN=", "API_ORIGIN="),
    "API_ORIGIN: no tag line",
  ],
  [
    "a variable nothing in the web app reads",
    replaced("API_ORIGIN=", "# > apps=web kind=config need=optional scopes=all\nMADE_UP=1\nAPI_ORIGIN="),
    "MADE_UP: tagged apps=web, but the web app reads no variable of that name",
  ],
  [
    "baked dropped from a variable the build fixes",
    replaced(API_ORIGIN, API_ORIGIN.replace(" baked", "")),
    "API_ORIGIN: changing it changes what next.config.ts returns",
  ],
  [
    "baked on a variable read at run time",
    replaced(
      "# > apps=both kind=secret need=always scopes=all\nCLERK_SECRET_KEY=",
      "# > apps=both kind=secret baked need=always scopes=all\nCLERK_SECRET_KEY=",
    ),
    "CLERK_SECRET_KEY: changing it changes nothing next.config.ts returns",
  ],
  [
    "a public variable not tagged baked",
    replaced(
      "# > apps=web kind=public baked need=mode scopes=all\nNEXT_PUBLIC_SENTRY_DSN=",
      "# > apps=web kind=public need=mode scopes=all\nNEXT_PUBLIC_SENTRY_DSN=",
    ),
    "NEXT_PUBLIC_SENTRY_DSN: Next.js inlines it",
  ],
  [
    "a public variable not tagged public",
    replaced(
      "# > apps=web kind=public baked need=mode scopes=all\nNEXT_PUBLIC_SENTRY_DSN=",
      "# > apps=web kind=config baked need=mode scopes=all\nNEXT_PUBLIC_SENTRY_DSN=",
    ),
    "NEXT_PUBLIC_SENTRY_DSN: the browser bundle receives it",
  ],
  [
    "a derived variable written as a setting",
    replaced(
      "API_ORIGIN=",
      "# > apps=web kind=public baked need=optional scopes=all\nNEXT_PUBLIC_APP_ENV=\nAPI_ORIGIN=",
    ),
    "NEXT_PUBLIC_APP_ENV: next.config.ts derives it",
  ],
  [
    "a derived variable missing from the header",
    replaced("NEXT_PUBLIC_ERROR_REPORTING_ENABLED,", ""),
    "NEXT_PUBLIC_ERROR_REPORTING_ENABLED: next.config.ts sets it",
  ],
  [
    "a derived variable the config no longer sets",
    replaced("NEXT_PUBLIC_APP_ENV, ", "NEXT_PUBLIC_APP_ENV, NEXT_PUBLIC_MADE_UP, "),
    "NEXT_PUBLIC_MADE_UP: the header lists it as derived",
  ],
  [
    "a platform variable missing from the header",
    replaced("VERCEL, VERCEL_ENV, ", "VERCEL, "),
    "VERCEL_ENV: Vercel sets it and the web app reads it",
  ],
];

describe("each kind of drift", () => {
  it.each(DRIFT)("%s is reported by name", async (_name, edit, expected) => {
    const found = problems(edit(example), await gatherFacts());
    expect(
      found.some((line) => line.includes(expected)),
      found.join("\n"),
    ).toBe(true);
  });

  it("includes a server variable the test has no changed environment for", async () => {
    const facts = await gatherFacts();
    const without = { ...facts.bakedByConfig };
    delete without.API_ORIGIN;
    const found = problems(example, { ...facts, bakedByConfig: without });
    expect(found.some((line) => line.startsWith("API_ORIGIN: add an environment that changes only it"))).toBe(
      true,
    );
  });

  it("reads the tag above a variable, or the default of its section", () => {
    const entries = parseEntries(example);
    expect(entries.get("ANALYTICS_MODE")?.tag).toEqual({ apps: "both", kind: "config", baked: true });
    expect(entries.get("CLERK_API_URL")?.tag).toMatchObject({ apps: "api", kind: "config" });
    expect(entries.get("OUTBOX_LEASE_SECONDS")?.tag).toEqual({ apps: "api", kind: "config", baked: false });
    expect(entries.get("E2E_DATABASE_URL")?.tag).toBeNull();
  });
});
