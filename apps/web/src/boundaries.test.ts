// @vitest-environment node
import path from "node:path";

import { ESLint } from "eslint";

/**
 * Proves the import-boundary rules in eslint.config.mjs fire. It uses the
 * project's real configuration and real file paths, so imports resolve exactly
 * as they do under `pnpm lint`.
 */
const webRoot = path.resolve(import.meta.dirname, "..");
const BOUNDARY_RULE = "import/no-restricted-paths";

let eslint: ESLint;
beforeAll(() => {
  eslint = new ESLint({ cwd: webRoot });
});

async function errors(file: string, code: string) {
  const [result] = await eslint.lintText(code, { filePath: path.join(webRoot, file) });
  return (result?.messages ?? []).filter((message) => message.severity === 2);
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
});
