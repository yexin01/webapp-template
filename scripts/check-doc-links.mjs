#!/usr/bin/env node
/**
 * Relative Markdown links must resolve to a file or folder (anchors are not
 * checked). Also run on an initialized copy by test:init-template, which
 * removes template material and the links to it.
 *
 *   pnpm check:links
 */
import path from "node:path";

import { brokenLinks } from "./lib/doc-links.mjs";

const root = process.env.TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, "..");
const broken = brokenLinks(root);
if (broken.length) {
  console.error("Markdown links that do not resolve:");
  for (const { file, target } of broken) console.error(`- ${file}: ${target}`);
  process.exit(1);
}
console.log("Every relative Markdown link resolves.");
