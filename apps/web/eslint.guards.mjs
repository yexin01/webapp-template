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
const serverOnlyMessage =
  "Server-only configuration: import it only from next.config.ts and server instrumentation (ENV-03).";
const serverOnlyModules = {
  group: ["**/env.server", "**/env.server.*", "**/env-files", "**/env-files.*"],
  message: serverOnlyMessage,
};
// createRequire() builds a require function that the require() selectors below cannot see.
const moduleLoaderMessage =
  "Load modules with import; createRequire would bypass the import guards (ENV-03).";
const moduleLoaders = ["module", "node:module"].map((name) => ({ name, message: moduleLoaderMessage }));

/** The banned package names as one esquery regular expression, for import() and require(). */
function packagePattern() {
  const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const names = bannedPackages
    .flatMap(({ group }) => group)
    .map((name) => (name.endsWith("/*") ? escape(name.slice(0, -1)) : `${escape(name)}(?:/|$)`));
  // esquery ends a regular expression at the first "/", so slashes are written as \x2F.
  return `/^(?:${names.join("|").replaceAll("/", "\\x2F")})/`;
}

/**
 * `import()` and `require()` of a module whose specifier matches an esquery
 * regular expression, written as a string or as a template literal without
 * substitutions (`import(\`stripe\`)`).
 */
const loadsOf = (pattern, message) =>
  ["ImportExpression > .source", "CallExpression[callee.name='require'] > .arguments:first-child"].flatMap(
    (specifier) => [
      { selector: `${specifier}[value=${pattern}]`, message },
      {
        selector: `${specifier}[type='TemplateLiteral'][expressions.length=0][quasis.0.value.cooked=${pattern}]`,
        message,
      },
    ],
  );

const fe02Message = "FE-02: this package is used only by the Python API (billing, database, or LLM).";
const dynamicImports = [
  ...loadsOf(packagePattern(), fe02Message),
  // The process module, which no file may import (processModules), loaded dynamically.
  ...loadsOf("/^(?:node:)?process$/", processModules[0].message),
];
// The server-only configuration modules (serverOnlyModules), loaded dynamically.
const serverOnlyLoads = loadsOf(
  "/(?:^|\\x2F)env(?:\\.server|-files)(?:\\.[cm]?[jt]sx?)?$/",
  serverOnlyMessage,
);
// The module loader (moduleLoaders), loaded dynamically.
const moduleLoaderLoads = loadsOf("/^(?:node:)?module$/", moduleLoaderMessage);

// ENV-07: application code may read only NEXT_PUBLIC_* (inlined by Next.js at
// build time) and NODE_ENV, and only as `process.env.NAME`. The other ways to
// reach the environment written out in source are rejected below: they could
// read a server-only value, or read a public one in a form the build cannot
// inline. Lint cannot follow a key computed at run time; the bundle scan
// (scripts/check-bundle-secrets.mjs) remains the backstop.
const envMessage = "Only NEXT_PUBLIC_* variables may be read in application code (ENV-07).";
/** The names of the global object, through which process can be reached. */
const globalObjects = "/^(?:globalThis|global|window|self)$/";
/**
 * Attribute selectors for a string literal, or a template literal without substitutions, whose
 * text is `name`, on the node itself or on its `field` (for example "property.").
 */
const stringNamed = (name, field = "") => [
  `[${field}value='${name}']`,
  `[${field}type='TemplateLiteral'][${field}expressions.length=0][${field}quasis.0.value.cooked='${name}']`,
];
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
    selector: `MemberExpression[object.name=${globalObjects}][property.name='process']`,
    message: envMessage,
  },
  {
    // globalThis["process"], window[key]: a computed key can name process.
    selector: `MemberExpression[object.name=${globalObjects}][computed=true]`,
    message: `${envMessage} Write globalThis.name, not a computed key.`,
  },
  {
    // globalThis.globalThis, window.self: the global object reached through itself.
    selector: `MemberExpression[computed=false][object.name=${globalObjects}][property.name=${globalObjects}]`,
    message: `${envMessage} Name the global object directly, not through itself.`,
  },
  {
    selector: `:matches(VariableDeclarator[init.name=${globalObjects}], AssignmentExpression[right.name=${globalObjects}]) > ObjectPattern > Property:matches([key.name='process'], [key.value='process'])`,
    message: `${envMessage} Do not destructure process from the global object.`,
  },
  // Reflect.get(window, "process"), Reflect.get(self, `process`), and the other Reflect lookups.
  ...stringNamed("process").map((key) => ({
    selector: `CallExpression[callee.object.name='Reflect'] > .arguments${key}`,
    message: envMessage,
  })),
];

// The global object itself anywhere but in `globalThis.name`: aliased (`const g = globalThis`),
// destructured, or passed (`Reflect.get(globalThis, "process")`). That is globalThis, Node's
// global, or self, the global object of the edge runtime, whose process.env holds the server's
// variables. Not window: it exists only in the browser, where process.env has only public values.
// Type positions (`typeof globalThis`, `declare global`) and `typeof self` checks expose nothing.
const globalObjectReferences = [
  {
    selector: [
      "Identifier[name=/^(?:globalThis|global|self)$/]",
      ":not(MemberExpression > Identifier.object)",
      ":not(MemberExpression[computed=false] > Identifier.property)",
      ":not(Property[computed=false] > Identifier.key)",
      ":not(TSTypeQuery > Identifier.exprName)",
      ":not(TSQualifiedName > Identifier.left)",
      ":not(TSModuleDeclaration > Identifier.id)",
      ":not(UnaryExpression[operator='typeof'] > Identifier.argument)",
    ].join(""),
    message: `${envMessage} Use the global object only as globalThis.name; do not alias, destructure, or pass it.`,
  },
];

// Function runs code in strings, which no rule here can read. no-new-func sees only
// `Function(…)` and `new Function(…)`, not Function by other names: aliased or passed on
// (no-restricted-globals below), through the global object, or as a function's constructor
// (`(() => 0).constructor`). Reading a constructor's name, comparing constructors, and a
// class constructing its own kind (`new this.constructor()`) expose nothing.
const functionMessage = "Do not use Function or a function's constructor: they run code in strings (ENV-07).";
const functionReferences = [
  {
    selector: `MemberExpression[object.name=${globalObjects}][property.name='Function']`,
    message: functionMessage,
  },
  {
    selector: [
      `MemberExpression:matches(${[
        "[computed=false][property.name='constructor']",
        ...stringNamed("constructor", "property.").map((key) => `[computed=true]${key}`),
      ].join(", ")})`,
      ":not([object.type='ThisExpression'])",
      ":not(MemberExpression[computed=false][property.name='name'] > MemberExpression.object)",
      ":not(BinaryExpression > MemberExpression)",
    ].join(""),
    message: functionMessage,
  },
  ...stringNamed("constructor").map((key) => ({
    selector: `CallExpression[callee.object.name='Reflect'] > .arguments${key}`,
    message: functionMessage,
  })),
];

// `process` itself anywhere but in `process.env.NAME` (checked above): aliased or destructured
// (`const { env } = process`), passed (`Reflect.get(process, "env")`), or another property.
// A property named "process" (`obj.process`, `{ process: 1 }`) is not the global.
const processReferences = [
  {
    selector: [
      "Identifier[name='process']",
      ":not(MemberExpression[computed=false][property.name='env'] > Identifier.object)",
      ":not(MemberExpression[computed=false] > Identifier.property)",
      ":not(Property[computed=false] > Identifier.key)",
    ].join(""),
    message: `${envMessage} Read process.env.NEXT_PUBLIC_NAME directly; do not alias, destructure, or pass process.`,
  },
];

/** Files that run only on the server or in tooling, and may read server-only variables. */
export const serverFiles = [
  "next.config.ts",
  "playwright.config.ts",
  "eslint.*.mjs",
  "src/instrumentation.ts",
  "src/sentry.*.ts",
  "e2e/**",
  "scripts/**",
];

/** Tests of the server-only configuration modules may import them. */
const serverConfigTests = ["src/lib/env.server.test.ts", "src/lib/env-files.test.ts"];

/**
 * Tests run in Node and never reach the browser bundle, so they may use
 * `process` for tooling (for example `process.execPath`). Every other rule
 * still applies to them, including the process.env rules.
 */
const testFiles = ["**/*.test.ts", "**/*.test.tsx"];

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
          { paths: [...processModules, ...moduleLoaders], patterns: [...bannedPackages, serverOnlyModules] },
        ],
        "no-restricted-syntax": [
          "error",
          ...environmentReads,
          ...processReferences,
          ...globalObjectReferences,
          ...functionReferences,
          ...serverOnlyLoads,
          ...moduleLoaderLoads,
          ...dynamicImports,
        ],
        // Code in strings is invisible to every rule above.
        "no-eval": "error",
        "no-implied-eval": "error",
        "no-new-func": "error",
        "no-restricted-globals": ["error", { name: "Function", message: functionMessage }],
      },
    },
    {
      files: testFiles,
      rules: {
        "no-restricted-syntax": [
          "error",
          ...environmentReads,
          ...serverOnlyLoads,
          ...moduleLoaderLoads,
          ...dynamicImports,
        ],
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
        "no-restricted-syntax": ["error", ...environmentReads, ...dynamicImports],
      },
    },
  ];
}
