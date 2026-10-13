#!/usr/bin/env node
/**
 * Template initialization (REPO-07): renames project identifiers and creates a
 * local .env from .env.example. Never copies credentials. Safe to re-run.
 *
 *   pnpm init-template --name "Acme Cloud" --slug acme-cloud
 *   pnpm init-template --dry-run --name "Acme Cloud" --slug acme-cloud
 */
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");
const { values } = parseArgs({
  options: {
    name: { type: "string" },
    slug: { type: "string" },
    "dry-run": { type: "boolean", default: false },
  },
});

const name = values.name?.trim();
const slug = values.slug?.trim();
if (!name || !slug || !/^[a-z][a-z0-9-]{1,40}$/.test(slug) || /["<>`$\\]/.test(name)) {
  console.error('Usage: pnpm init-template --name "Product Name" --slug product-slug  (slug: a-z, 0-9, -)');
  process.exit(2);
}

const ORIGINAL_NAME = "SaaS Template";
const ORIGINAL_SLUG = "saas-template";
const targets = [
  "package.json",
  "apps/web/src/app/layout.tsx",
  "apps/web/src/components/site-header.tsx",
  "apps/api/app/http/app.py",
  "packages/contracts/openapi.json",
  "README.md",
];

const changes = [];
for (const rel of targets) {
  const file = path.join(root, rel);
  if (!existsSync(file)) continue;
  const before = readFileSync(file, "utf8");
  const after = before.replaceAll(ORIGINAL_NAME, name).replaceAll(ORIGINAL_SLUG, slug);
  if (after !== before) {
    changes.push(rel);
    if (!values["dry-run"]) writeFileSync(file, after);
  }
}

const envFile = path.join(root, ".env");
const envCreated = !existsSync(envFile);
if (envCreated && !values["dry-run"]) copyFileSync(path.join(root, ".env.example"), envFile);

console.log(JSON.stringify({ dryRun: values["dry-run"], updated: changes, envCreated }, null, 2));
if (envCreated) console.log("Next: fill in .env with your own sandbox keys (see docs/integrations.md).");
