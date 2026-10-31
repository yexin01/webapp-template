import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";

const MODE_FILES = [".env.development", ".env.production", ".env.test"].flatMap((file) => [
  file,
  `${file}.local`,
]);

/**
 * Loads the repository-root .env into `target` (values already in `target`
 * win), except that only the variables `include` accepts are loaded. No
 * variable expansion.
 *
 * Only the root .env is read, like the API (config.py ENV_FILE) and
 * scripts/check-bundle-secrets.mjs, so both apps and the bundle scan see one
 * configuration. The loader copies only the variables the web app reads, so
 * this file gives a local web process none of the API's secrets
 * (STRIPE_SECRET_KEY, DATABASE_URL, LLM_API_KEY, CRON_SECRET, ...). On Vercel
 * the platform sets the process environment, and one project gives every
 * service every variable (docs/deployment.md). Server-only: next.config.ts is
 * the one caller.
 *
 * @returns the names of the variables it set
 */
export function loadEnvFiles(
  dir: string,
  include: (name: string) => boolean,
  target: Record<string, string | undefined>,
): string[] {
  let text: string;
  try {
    text = readFileSync(path.join(dir, ".env"), "utf8");
  } catch {
    return [];
  }
  const loaded: string[] = [];
  for (const [name, value] of Object.entries(parseEnv(text))) {
    if (!include(name) || target[name] !== undefined || value === undefined) continue;
    target[name] = value;
    loaded.push(name);
  }
  return loaded;
}

/**
 * The environment files that would split the configuration in two, by name (never their
 * contents): in the repository root, every .env variant except .env, which is the one file
 * read; in the web app's directory, every one, because Next.js loads those itself,
 * unfiltered, and the API never sees them. Exact names only, so .env.example and synced
 * copies such as ".env 2" never match.
 */
export function strayEnvFiles(repoRoot: string, webDir: string): string[] {
  const root = [".env.local", ...MODE_FILES].map((file) => ({ file, display: file, dir: repoRoot }));
  const web = [".env", ".env.local", ...MODE_FILES].map((file) => ({
    file,
    display: `apps/web/${file}`,
    dir: webDir,
  }));
  return [...root, ...web]
    .filter(({ file, dir }) => existsSync(path.join(dir, file)))
    .map(({ display }) => display);
}
