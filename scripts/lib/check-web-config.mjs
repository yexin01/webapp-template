/**
 * Runs the web app's own configuration check: next.config.ts, loaded by Next.js
 * the way `next dev` loads it, calls loadServerEnv and throws its ConfigError.
 * Prints one line, `WEBCONFIG:` and JSON, and exits 0:
 * - {"ok":true}
 * - {"ok":false,"problems":[...]}   variable names only, never values
 * - {"ok":null,"skipped":"..."}     the check could not run (it then does not block `pnpm dev`)
 *
 *   node scripts/lib/check-web-config.mjs apps/web
 */
import { createRequire } from "node:module";
import path from "node:path";

const dir = path.resolve(process.argv[2] ?? "apps/web");
const say = (result) => console.log(`WEBCONFIG:${JSON.stringify(result)}`);

let loadConfig;
let phase;
try {
  const require = createRequire(path.join(dir, "package.json"));
  loadConfig = require("next/dist/server/config").default;
  phase = require("next/constants").PHASE_DEVELOPMENT_SERVER;
  if (typeof loadConfig !== "function" || typeof phase !== "string") throw new Error("unexpected shape");
} catch {
  say({ ok: null, skipped: "this Next.js version has no config loader the check knows (next dev checks the configuration itself)" });
  process.exit(0);
}

try {
  await loadConfig(phase, dir);
  say({ ok: true });
} catch (error) {
  if (Array.isArray(error?.problems) && error.problems.every((problem) => typeof problem === "string")) {
    say({ ok: false, problems: error.problems });
  } else {
    say({ ok: false, problems: [`next.config.ts could not be loaded: ${String(error?.message).split("\n")[0].slice(0, 200)}`] });
  }
}
process.exit(0);
