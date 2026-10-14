import { createRequire } from "node:module";
import path from "node:path";

/**
 * Lint guards for FE-02, ENV-03, and ENV-07, plus the import resolver setup
 * the boundary rules depend on. eslint.config.mjs spreads `guardConfigs()`
 * after its own entries, so these rule settings are the ones in force.
 */

// FE-02: the web app holds no business logic, server secrets, or direct
// database / payment-provider access. Those packages are banned outright,
// whether imported statically, dynamically, or with require().
export const bannedPackages = [
  { group: ["stripe", "@stripe/*"], message: "Billing goes through the Python API." },
  {
    group: ["@supabase/*", "pg", "postgres", "@prisma/*", "drizzle-orm"],
    message: "The browser never talks to the database (DEC-008).",
  },
  {
    group: ["openai", "@anthropic-ai/*", "ai"],
    message: "LLM providers are called only by the Python API.",
  },
];

// ENV-03: configuration is read through src/lib/env.ts (public) and
// src/lib/env.server.ts (server-only), never through node:process.
const processModules = ["process", "node:process"].map((name) => ({
  name,
  message: "Read configuration through @/lib/env (ENV-03, ENV-07).",
}));
const serverOnlyModules = {
  group: ["**/env.server", "**/env-files"],
  message:
    "Server-only configuration: import it only from next.config.ts and server instrumentation (ENV-03).",
};

/** The banned package names as one esquery regular expression, for import() and require(). */
function packagePattern() {
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const names = bannedPackages
    .flatMap(({ group }) => group)
    .map((name) => (name.endsWith("/*") ? escape(name.slice(0, -1)) : `${escape(name)}(?:/|$)`));
  // esquery ends a regular expression at the first "/", so slashes are written as \x2F.
  return `/^(?:${names.join("|").replaceAll("/", "\\x2F")})/`;
}

const fe02Message = "FE-02: this package is used only by the Python API (billing, database, or LLM).";
const dynamicImports = [
  { selector: `ImportExpression[source.value=${packagePattern()}]`, message: fe02Message },
  {
    selector: `CallExpression[callee.name='require'][arguments.0.value=${packagePattern()}]`,
    message: fe02Message,
  },
];

// ENV-07: application code may read only NEXT_PUBLIC_* (inlined by Next.js at
// build time) and NODE_ENV, and only as `process.env.NAME`. Every other way to
// reach the environment is rejected: it could read a server-only value, or
// read a public one in a form the build cannot inline.
const envMessage = "Only NEXT_PUBLIC_* variables may be read in application code (ENV-07).";
const environmentReads = [
  {
    selector:
      "MemberExpression[object.object.name='process'][object.property.name='env'][property.name=/^(?!NEXT_PUBLIC_|NODE_ENV$).*/]",
    message: envMessage,
  },
  {
    selector: "MemberExpression[computed=true][object.object.name='process'][object.property.name='env']",
    message: `${envMessage} Write process.env.NEXT_PUBLIC_NAME so Next.js can inline it.`,
  },
  {
    // process.env used other than as `process.env.NAME`: destructured, aliased, spread, or passed.
    selector: ":not(MemberExpression) > MemberExpression[object.name='process'][property.name='env']",
    message: `${envMessage} Do not destructure, alias, or pass process.env.`,
  },
  { selector: "MemberExpression[object.name='process'][computed=true]", message: envMessage },
  {
    selector: "MemberExpression[object.name=/^(?:globalThis|global|window|self)$/][property.name='process']",
    message: envMessage,
  },
];

/** Files that run only on the server or in tooling, and may read server-only variables. */
export const serverFiles = [
  "next.config.ts",
  "playwright.config.ts",
  "src/instrumentation.ts",
  "src/sentry.*.ts",
  "e2e/**",
  "scripts/**",
];

/** Tests of the server-only configuration modules may import them. */
const serverConfigTests = ["src/lib/env.server.test.ts", "src/lib/env-files.test.ts"];

/**
 * @param {string} root the web app directory
 * @returns {import("eslint").Linter.Config[]}
 */
export function guardConfigs(root) {
  // eslint-config-next registers the TypeScript resolver under its resolved
  // path. Without an explicit project, the resolver reads tsconfig.json from
  // the process's working directory, so linting from anywhere but apps/web
  // left "@/..." imports unresolved and the boundary rules silently passed.
  const fromNextConfig = createRequire(createRequire(import.meta.url).resolve("eslint-config-next"));
  const typescriptResolver = fromNextConfig.resolve("eslint-import-resolver-typescript");

  return [
    {
      settings: {
        "import/resolver": {
          [typescriptResolver]: { alwaysTryTypes: true, project: path.join(root, "tsconfig.json") },
        },
      },
      rules: {
        "no-restricted-imports": [
          "error",
          { paths: processModules, patterns: [...bannedPackages, serverOnlyModules] },
        ],
        "no-restricted-syntax": ["error", ...environmentReads, ...dynamicImports],
      },
    },
    {
      files: serverFiles,
      rules: {
        "no-restricted-imports": ["error", { paths: processModules, patterns: bannedPackages }],
        "no-restricted-syntax": ["error", ...dynamicImports],
      },
    },
    {
      files: serverConfigTests,
      rules: {
        "no-restricted-imports": ["error", { paths: processModules, patterns: bannedPackages }],
      },
    },
  ];
}
