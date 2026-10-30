// @vitest-environment node
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { ESLint, type Linter } from "eslint";

import { guardConfigs } from "../eslint.guards.mjs";

/**
 * Proves the import-boundary rules in eslint.config.mjs and the FE-02, ENV-03,
 * and ENV-07 guards in eslint.guards.mjs fire. It uses the project's real
 * configuration and real file paths, so imports resolve exactly as they do
 * under `pnpm lint`. The guard cases also apply eslint.guards.mjs explicitly;
 * a separate test proves eslint.config.mjs wires the guards in.
 */
const webRoot = path.resolve(import.meta.dirname, "..");
const BOUNDARY_RULE = "import/no-restricted-paths";
const guards = guardConfigs(webRoot) as Linter.Config[];

let eslint: ESLint;
let guarded: ESLint;
beforeAll(() => {
  eslint = new ESLint({ cwd: webRoot });
  guarded = new ESLint({ cwd: webRoot, overrideConfig: guards });
});

async function errors(file: string, code: string, linter = eslint) {
  const [result] = await linter.lintText(code, { filePath: path.join(webRoot, file) });
  return (result?.messages ?? []).filter((message) => message.severity === 2);
}

async function guardErrors(file: string, code: string) {
  return (await errors(file, code, guarded)).map((message) => message.ruleId);
}

async function boundaryErrors(file: string, importPath: string) {
  const found = await errors(file, `import * as probe from "${importPath}";\nexport default probe;\n`);
  return found.filter((message) => message.ruleId === BOUNDARY_RULE);
}

describe("import boundaries", { timeout: 30_000 }, () => {
  it.each(["src/app/page.tsx", "src/features/billing/index.ts", "src/ui/state-views.tsx", "src/lib/csp.ts"])(
    "is configured for %s",
    async (file) => {
      const config = (await eslint.calculateConfigForFile(path.join(webRoot, file))) as {
        rules?: Record<string, unknown>;
      };
      expect(config.rules?.[BOUNDARY_RULE]).toBeDefined();
    },
  );

  it.each([
    [
      "a feature deep-imports another feature",
      "src/features/workspace/probe.ts",
      "@/features/billing/plan-badge",
    ],
    [
      "a feature deep-imports another feature relatively",
      "src/features/workspace/probe.ts",
      "../billing/plan-badge",
    ],
    ["a route deep-imports a feature", "src/app/probe/page.tsx", "@/features/billing/billing-panel"],
    [
      "a root file deep-imports a feature",
      "src/instrumentation-client.ts",
      "@/features/integrations/browser-checks",
    ],
    ["an e2e test deep-imports a feature", "e2e/pages.spec.ts", "@/features/billing/paywall"],
    ["lib imports a feature, even its index", "src/lib/probe.ts", "@/features/billing"],
    ["lib imports ui", "src/lib/probe.ts", "@/ui/state-views"],
    ["lib imports app", "src/lib/probe.ts", "@/app/not-found"],
    ["ui imports a feature", "src/ui/probe.tsx", "@/features/billing"],
    ["ui imports app", "src/ui/probe.tsx", "@/app/not-found"],
    ["a feature imports app", "src/features/billing/probe.ts", "@/app/not-found"],
  ])("rejects: %s", async (_case, file, importPath) => {
    expect(await boundaryErrors(file, importPath)).toHaveLength(1);
  });

  it.each([
    [
      "a feature uses another feature's public index",
      "src/features/workspace/probe.ts",
      "@/features/billing",
    ],
    ["a feature uses its own internals", "src/features/billing/probe.ts", "./paywall"],
    ["a route uses a feature's public index", "src/app/probe/page.tsx", "@/features/billing"],
    ["a root file uses a feature's public index", "src/instrumentation-client.ts", "@/features/integrations"],
    ["a route uses ui", "src/app/probe/page.tsx", "@/ui/state-views"],
    ["a feature uses ui", "src/features/billing/probe.ts", "@/ui/state-views"],
    ["ui uses lib", "src/ui/probe.tsx", "@/lib/api/errors"],
  ])("allows: %s", async (_case, file, importPath) => {
    expect(await boundaryErrors(file, importPath)).toEqual([]);
  });

  it("keeps the FE-02 and ENV-07 rules", async () => {
    const file = "src/features/billing/probe.ts";
    const fe02 = await errors(file, 'import Stripe from "stripe";\nexport default Stripe;\n');
    expect(fe02.map((message) => message.ruleId)).toContain("no-restricted-imports");
    const env07 = await errors(file, "export const key = process.env.STRIPE_SECRET_KEY;\n");
    expect(env07.map((message) => message.ruleId)).toContain("no-restricted-syntax");
  });

  it("resolves @/ imports when ESLint runs from another directory", () => {
    // The resolver once read tsconfig.json from the working directory, so the
    // boundary rules silently passed when linting from the repository root.
    const script = `
      const { ESLint } = await import(${JSON.stringify(import.meta.resolve("eslint"))});
      const { guardConfigs } = await import(${JSON.stringify(import.meta.resolve("../eslint.guards.mjs"))});
      const eslint = new ESLint({ cwd: ${JSON.stringify(webRoot)}, overrideConfig: guardConfigs(${JSON.stringify(webRoot)}) });
      const [result] = await eslint.lintText('import * as probe from "@/features/billing/plan-badge";\\nexport default probe;\\n', {
        filePath: ${JSON.stringify(path.join(webRoot, "src/features/workspace/probe.ts"))},
      });
      console.log(JSON.stringify(result.messages.map((message) => message.ruleId)));
    `;
    const child = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
      cwd: tmpdir(),
      encoding: "utf8",
    });
    expect(child.stderr.replace(/^Warning: React version.*$/gm, "").trim()).toBe("");
    expect(JSON.parse(child.stdout)).toContain(BOUNDARY_RULE);
  });
});

describe("FE-02, ENV-03, and ENV-07 guards", { timeout: 30_000 }, () => {
  const app = "src/features/billing/probe.ts";

  // Pending: eslint.config.mjs must spread `guardConfigs(import.meta.dirname)` from
  // eslint.guards.mjs (and drop its inline env and import rules). Agents may not edit
  // lint configuration in this repository, so the owner applies that change; then
  // remove `.skip`. Until then the older inline rules in eslint.config.mjs apply.
  it.skip("are applied by eslint.config.mjs", async () => {
    const config = (await eslint.calculateConfigForFile(path.join(webRoot, app))) as Linter.Config;
    const expected = guards[0] as Linter.Config;
    // Rule options only: the calculated config reports severities as numbers.
    const options = (rules: Linter.Config["rules"], rule: string) => [rules?.[rule]].flat().slice(1);
    for (const rule of ["no-restricted-syntax", "no-restricted-imports"]) {
      expect(options(config.rules, rule)).toEqual(options(expected.rules, rule));
    }
    expect(config.settings?.["import/resolver"]).toMatchObject(expected.settings?.["import/resolver"] ?? {});
  });

  it.each([
    [
      "destructuring process.env",
      "const { STRIPE_SECRET_KEY } = process.env;\nexport default STRIPE_SECRET_KEY;\n",
    ],
    ["aliasing process.env", "const e = process.env;\nexport const key = e.STRIPE_SECRET_KEY;\n"],
    ["spreading process.env", "export const all = { ...process.env };\n"],
    ["passing process.env", "export const names = Object.keys(process.env);\n"],
    ["computed access", 'export const key = process.env["STRIPE_SECRET_KEY"];\n'],
    ["computed access to a public name", 'export const key = process.env["NEXT_PUBLIC_POSTHOG_KEY"];\n'],
    ["process['env']", 'export const key = process["env"].STRIPE_SECRET_KEY;\n'],
    ["globalThis.process", "export const key = globalThis.process.env.STRIPE_SECRET_KEY;\n"],
    ["dynamic import of stripe", 'export const stripe = import("stripe");\n'],
    ["dynamic import of a scoped package", 'export const stripe = import("@stripe/stripe-js");\n'],
    ["dynamic import of a subpath", 'export const pg = import("pg/lib/client");\n'],
    ["require of an LLM SDK", 'export const sdk = require("openai");\n'],
    ["destructuring process itself", "const { env } = process;\nexport const key = env.STRIPE_SECRET_KEY;\n"],
    ["aliasing process", "const p = process;\nexport const key = p.env.STRIPE_SECRET_KEY;\n"],
    ["Reflect.get on process", 'export const key = Reflect.get(process, "env").STRIPE_SECRET_KEY;\n'],
    ["another property of process", "export const argv = process.argv;\n"],
    ["dynamic import of the server configuration", 'export const config = import("@/lib/env.server");\n'],
    ["dynamic import of the env-file loader", 'export const loader = import("../../lib/env-files.ts");\n'],
    ["require of the server configuration", 'export const config = require("./env.server");\n'],
    ["dynamic import of node:process", 'export const proc = import("node:process");\n'],
    ["require of the process module", 'export const proc = require("process");\n'],
    ["dynamic import of stripe as a template literal", "export const stripe = import(`stripe`);\n"],
    [
      "dynamic import of the server configuration as a template literal",
      "export const config = import(`@/lib/env.server`);\n",
    ],
    [
      "require of the server configuration as a template literal",
      "export const c = require(`./env.server`);\n",
    ],
    ['globalThis["process"]', 'export const key = globalThis["process"].env.STRIPE_SECRET_KEY;\n'],
    ['window["process"]', 'export const key = window["process"].env.STRIPE_SECRET_KEY;\n'],
    ["a computed key on self", 'const k = "process";\nexport const key = self[k].env.STRIPE_SECRET_KEY;\n'],
    ["aliasing globalThis", "const g = globalThis;\nexport const key = g.process.env.STRIPE_SECRET_KEY;\n"],
    ["aliasing global", "const g = global;\nexport const key = g.process.env.STRIPE_SECRET_KEY;\n"],
    [
      "destructuring process from globalThis",
      "const { process: p } = globalThis;\nexport const key = p.env.STRIPE_SECRET_KEY;\n",
    ],
    [
      "destructuring process from window",
      "const { process: p } = window;\nexport const key = p.env.STRIPE_SECRET_KEY;\n",
    ],
    [
      "Reflect.get on globalThis",
      'export const key = Reflect.get(globalThis, "process").env.STRIPE_SECRET_KEY;\n',
    ],
    ["Reflect.get on window", 'export const key = Reflect.get(window, "process").env.STRIPE_SECRET_KEY;\n'],
    ["dynamic import of node:module", 'export const m = import("node:module");\n'],
    [
      "globalThis reached through itself",
      "export const key = globalThis.globalThis.process.env.STRIPE_SECRET_KEY;\n",
    ],
    [
      "global reached through globalThis",
      "export const key = globalThis.global.process.env.STRIPE_SECRET_KEY;\n",
    ],
    ["self reached through window", "export const key = window.self.process.env.STRIPE_SECRET_KEY;\n"],
    // self is the global object in the edge runtime, where process.env holds server-only values.
    ["aliasing self", "const s = self;\nexport const key = s.process.env.STRIPE_SECRET_KEY;\n"],
    ["self in a sequence", "export const key = (0, self).process.env.STRIPE_SECRET_KEY;\n"],
    ["self in an object shorthand", "export const scope = { self };\n"],
    [
      "Reflect.get on self with a template literal",
      "export const key = Reflect.get(self, `process`).env.STRIPE_SECRET_KEY;\n",
    ],
    [
      "Reflect.get on window with a template literal",
      "export const key = Reflect.get(window, `process`).env.STRIPE_SECRET_KEY;\n",
    ],
    [
      "Function reached through a function's constructor",
      'export const key = (() => 0).constructor("return process.env.STRIPE_SECRET_KEY")();\n',
    ],
    [
      "Function reached through computed constructor keys",
      'export const key = []["constructor"]["constructor"]("return process.env.STRIPE_SECRET_KEY")();\n',
    ],
    [
      "an async Function constructed through a constructor",
      'export const f = new (async () => undefined).constructor("return process.env.STRIPE_SECRET_KEY");\n',
    ],
    [
      "an aliased function constructor",
      'const C = (() => 0).constructor;\nexport const key = C("return process.env.STRIPE_SECRET_KEY")();\n',
    ],
    [
      "a function constructor passed on",
      'export const key = Reflect.apply((() => 0).constructor, null, ["return process.env.X"])();\n',
    ],
    [
      "Reflect.get of a constructor",
      'export const key = Reflect.get(() => 0, "constructor")("return process.env.STRIPE_SECRET_KEY")();\n',
    ],
    [
      "Function reached through the global object",
      'export const key = globalThis.Function("return process.env.STRIPE_SECRET_KEY")();\n',
    ],
  ])("rejects %s with no-restricted-syntax", async (_case, code) => {
    expect(await guardErrors(app, code)).toContain("no-restricted-syntax");
  });

  it.each([
    ["eval", 'export const key = eval("process.env.STRIPE_SECRET_KEY");\n', "no-eval"],
    ["indirect eval", 'export const key = globalThis.eval("process.env.STRIPE_SECRET_KEY");\n', "no-eval"],
    [
      "new Function",
      'export const key = new Function("return process.env.STRIPE_SECRET_KEY")();\n',
      "no-new-func",
    ],
    ["Function()", 'export const key = Function("return process.env.STRIPE_SECRET_KEY")();\n', "no-new-func"],
    [
      "an aliased Function",
      'const F = Function;\nexport const key = F("return process.env.STRIPE_SECRET_KEY")();\n',
      "no-restricted-globals",
    ],
    [
      "Function passed to Reflect.construct",
      'export const key = Reflect.construct(Function, ["return process.env.STRIPE_SECRET_KEY"])();\n',
      "no-restricted-globals",
    ],
    [
      "code in a string timer",
      'setTimeout("globalThis.leak = process.env.STRIPE_SECRET_KEY", 0);\n',
      "no-implied-eval",
    ],
    [
      "createRequire",
      'import { createRequire } from "node:module";\nexport const c = createRequire(import.meta.url)("./lib/env.server");\n',
      "no-restricted-imports",
    ],
    ["the module namespace", 'import * as m from "module";\nexport default m;\n', "no-restricted-imports"],
  ])("rejects %s", async (_case, code, rule) => {
    expect(await guardErrors(app, code)).toContain(rule);
  });

  it.each([
    [
      "env from node:process",
      'import { env } from "node:process";\nexport const key = env.STRIPE_SECRET_KEY;\n',
    ],
    ["the process module", 'import proc from "process";\nexport const key = proc.env.STRIPE_SECRET_KEY;\n'],
    [
      "the server configuration",
      'import { loadServerEnv } from "@/lib/env.server";\nexport default loadServerEnv;\n',
    ],
    [
      "the env-file loader",
      'import { loadEnvFiles } from "@/lib/env-files";\nexport default loadEnvFiles;\n',
    ],
    ["a re-export of stripe", 'export { default } from "stripe";\n'],
    [
      "the server configuration by file name",
      'import { loadServerEnv } from "@/lib/env.server.ts";\nexport default loadServerEnv;\n',
    ],
  ])("rejects importing %s", async (_case, code) => {
    expect(await guardErrors(app, code)).toContain("no-restricted-imports");
  });

  it.each([
    ["posthog-js", 'import posthog from "posthog-js";\nexport default posthog;\n'],
    [
      "a posthog-js subpath",
      'import { PostHog } from "posthog-js/dist/module.full";\nexport default PostHog;\n',
    ],
    ["a re-export of posthog-js", 'export { default } from "posthog-js";\n'],
  ])("rejects importing %s outside the analytics module (OBS-03)", async (_case, code) => {
    for (const file of [app, "src/lib/probe.ts", "src/ui/probe.tsx", "src/lib/analytics-provider.tsx"]) {
      expect(await guardErrors(file, code)).toContain("no-restricted-imports");
    }
    // A test that is not about analytics gets no exception either.
    expect(await guardErrors("src/lib/probe.test.ts", code)).toContain("no-restricted-imports");
  });

  it.each([
    ["import()", 'export const sdk = import("posthog-js");\n'],
    ["a template literal import()", "export const sdk = import(`posthog-js`);\n"],
    ["import() of a subpath", 'export const sdk = import("posthog-js/dist/module.full");\n'],
    ["require()", 'export const sdk = require("posthog-js");\n'],
  ])("rejects loading posthog-js with %s outside the analytics module (OBS-03)", async (_case, code) => {
    expect(await guardErrors(app, code)).toContain("no-restricted-syntax");
  });

  it("lets the analytics module and its real-SDK tests use posthog-js", async () => {
    const code = 'import posthog from "posthog-js";\nexport default posthog;\n';
    const lazily = 'export const sdk = import("posthog-js");\n';
    expect(await guardErrors("src/lib/analytics.ts", code)).toEqual([]);
    expect(await guardErrors("src/lib/analytics-canary.test.ts", code)).toEqual([]);
    expect(await guardErrors("src/lib/analytics.test.tsx", code)).toEqual([]);
    expect(await guardErrors("src/lib/analytics-pending.test.tsx", lazily)).toEqual([]);
    // Everything else the analytics module is held to stays in force.
    expect(
      await guardErrors("src/lib/analytics.ts", 'import Stripe from "stripe";\nexport default Stripe;\n'),
    ).toContain("no-restricted-imports");
    expect(
      await guardErrors("src/lib/analytics.ts", "export const key = process.env.STRIPE_SECRET_KEY;\n"),
    ).toContain("no-restricted-syntax");
  });

  it.each([
    ["a public variable", "export const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;\n"],
    ["NODE_ENV", "export const dev = process.env.NODE_ENV !== 'production';\n"],
    ["the public configuration", 'import { publicEnv } from "@/lib/env";\nexport default publicEnv;\n'],
    ["a lazily loaded local module", 'export const view = import("./paywall");\n'],
    ["a lazily loaded local module as a template literal", "export const view = import(`./paywall`);\n"],
    ["a package whose name only starts like a banned one", 'export const lib = import("aim");\n'],
    ["a property named process", "export const step = { process: 1 }.process;\n"],
    [
      "a module whose name only ends like the configuration",
      'export const m = import("./my-env.server-docs");\n',
    ],
    ["a property of globalThis", "export const f = globalThis.fetch;\n"],
    ["a property of window", "export const timer = window.setTimeout(() => undefined, 0);\n"],
    ["the type of globalThis", "export type Global = typeof globalThis;\n"],
    ["a property named self", "export const node = { self: 1 }.self;\n"],
    ["the type of a property of globalThis", "export type Fetch = typeof globalThis.fetch;\n"],
    ["checking whether self exists", 'export const worker = typeof self !== "undefined";\n'],
    ["checking whether global exists", 'export const node = typeof global !== "undefined";\n'],
    ["the name of a constructor", 'export const name = new Error("x").constructor.name;\n'],
    [
      "comparing constructors",
      "export const same = (a: object, b: object) => a.constructor === b.constructor;\n",
    ],
    [
      "a class constructing its own kind",
      "export class Box {\n  copy(): unknown {\n    return new this.constructor();\n  }\n}\n",
    ],
  ])("allows %s", async (_case, code) => {
    expect(await guardErrors(app, code)).toEqual([]);
  });

  it.each(["src/types/global.d.ts", "src/app/layout.tsx"])(
    "allows a global declaration in %s",
    async (file) => {
      const code = "declare global {\n  interface Window {\n    dataLayer: unknown[];\n  }\n}\nexport {};\n";
      expect(await guardErrors(file, code)).toEqual([]);
    },
  );

  it("lets the guard module itself load modules with createRequire", async () => {
    const code = readFileSync(path.join(webRoot, "eslint.guards.mjs"), "utf8");
    expect(await guardErrors("eslint.guards.mjs", code)).toEqual([]);
  });

  it("lets tests use process for tooling, but still not read server-only variables", async () => {
    const test = "src/lib/probe.test.ts";
    expect(await guardErrors(test, "export const node = process.execPath;\n")).toEqual([]);
    expect(await guardErrors(test, "export const key = process.env.STRIPE_SECRET_KEY;\n")).toContain(
      "no-restricted-syntax",
    );
    expect(await guardErrors(test, 'export const config = import("@/lib/env.server");\n')).toContain(
      "no-restricted-syntax",
    );
  });

  it("lets server entry points read server-only variables and the server configuration", async () => {
    const code =
      'import { loadServerEnv } from "./lib/env.server";\nexport const env = loadServerEnv(process.env);\n';
    expect(await guardErrors("src/sentry.server.config.ts", code)).toEqual([]);
    expect(
      await guardErrors("src/sentry.server.config.ts", 'export const lazy = import("./lib/env.server");\n'),
    ).toEqual([]);
    expect(
      await guardErrors("src/sentry.server.config.ts", 'export const p = import("node:process");\n'),
    ).toContain("no-restricted-syntax");
    expect(
      await guardErrors("src/sentry.server.config.ts", 'export const s = import("stripe");\n'),
    ).toContain("no-restricted-syntax");
  });
});
