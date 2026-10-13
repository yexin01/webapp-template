#!/usr/bin/env node
// Fails when the committed OpenAPI document or generated TypeScript types are
// out of date with the API code (DEC-007: the API schema is the source of truth).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const dir = mkdtempSync(path.join(tmpdir(), "contracts-"));
try {
  const spec = path.join(dir, "openapi.json");
  const types = path.join(dir, "schema.d.ts");
  execFileSync("uv", ["run", "python", "-m", "app.cli", "openapi", "--output", spec], {
    cwd: path.join(root, "apps/api"),
    stdio: "ignore",
  });
  execFileSync("pnpm", ["exec", "openapi-typescript", spec, "-o", types], {
    cwd: path.join(root, "apps/web"),
    stdio: "ignore",
  });
  const checks = [
    [spec, path.join(root, "packages/contracts/openapi.json")],
    [types, path.join(root, "apps/web/src/lib/api/schema.d.ts")],
  ];
  const stale = checks.filter(([fresh, committed]) => readFileSync(fresh, "utf8") !== readFileSync(committed, "utf8"));
  if (stale.length) {
    console.error("API contracts are out of date. Run `pnpm contracts` and commit the result:");
    for (const [, committed] of stale) console.error(`  - ${path.relative(root, committed)}`);
    process.exit(1);
  }
  console.log("API contracts are up to date.");
} finally {
  rmSync(dir, { recursive: true, force: true });
}
