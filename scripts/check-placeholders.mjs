#!/usr/bin/env node
/**
 * DEC-012 (ADR-0002): community contacts are adopter placeholders. In the
 * un-initialized template they are expected; once the project has been
 * renamed (package.json name changed), any remaining placeholder fails.
 *
 *   pnpm check:placeholders
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { ORIGINAL_SLUG, PLACEHOLDERS } from "./lib/template.mjs";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");
const packageName = JSON.parse(readFileSync(path.join(root, "package.json"), "utf8")).name;

const remaining = PLACEHOLDERS.filter(({ file, token }) => {
  const target = path.join(root, file);
  return existsSync(target) && readFileSync(target, "utf8").includes(token);
});

if (packageName === ORIGINAL_SLUG) {
  const list = remaining.map(({ file, token }) => `${file} (${token})`).join(", ") || "none";
  console.log(`Template mode: placeholders are expected until init-template runs. Present: ${list}.`);
} else if (remaining.length) {
  console.error("Community contact placeholders remain. Run init-template with the contact options, or edit:");
  for (const { file, token, option } of remaining) console.error(`- ${file}: ${token} (--${option})`);
  process.exit(1);
} else {
  console.log("Community contacts are filled in.");
}
