#!/usr/bin/env node
/**
 * Relative Markdown links must resolve to a file or folder, and a `#fragment`
 * to a heading or id of that Markdown file (GitHub's slug rules); so must the
 * `docs/x.md#fragment` pointers that code and workflows print or cite. Also run
 * on an initialized copy by test:init-template, which removes template material
 * and the links to it.
 *
 *   pnpm check:links
 */
import path from "node:path";

import { brokenLinks } from "./lib/doc-links.mjs";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");
const broken = brokenLinks(root);
if (broken.length) {
  console.error("Links that do not resolve:");
  for (const { file, target, reason } of broken) console.error(`- ${file}: ${target}${reason ? ` (${reason})` : ""}`);
  process.exit(1);
}
console.log("Every relative Markdown link and documentation pointer resolves.");
