import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const scripts = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, "../../package.json"), "utf8"),
).scripts;

test("pnpm dev:api serves with uvicorn's access log off: it prints each raw path, query string and client address", () => {
  // The API writes its own access line (request ID, route template, status), which holds none of them (OBS-04).
  assert.match(scripts["dev:api"], /\buvicorn\b.* --no-access-log\b/);
});
