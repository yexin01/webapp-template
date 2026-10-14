import { readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";

export type EnvMode = "development" | "production" | "test";

/**
 * Loads .env files from `dir` into `target` like Next.js does (same files,
 * same precedence; values already in `target` win), except that only the
 * variables `include` accepts are loaded. No variable expansion.
 *
 * One repository-root .env serves both apps. The web server loads only the
 * variables it reads, so API secrets (STRIPE_SECRET_KEY, DATABASE_URL,
 * LLM_API_KEY, CRON_SECRET, ...) never enter its process. Server-only:
 * next.config.ts is the one caller.
 *
 * @returns the names of the variables it set
 */
export function loadEnvFiles(
  dir: string,
  mode: EnvMode,
  include: (name: string) => boolean,
  target: Record<string, string | undefined>,
): string[] {
  const files = [`.env.${mode}.local`, ...(mode === "test" ? [] : [".env.local"]), `.env.${mode}`, ".env"];
  const loaded: string[] = [];
  for (const file of files) {
    let text: string;
    try {
      text = readFileSync(path.join(dir, file), "utf8");
    } catch {
      continue;
    }
    for (const [name, value] of Object.entries(parseEnv(text))) {
      if (!include(name) || target[name] !== undefined || value === undefined) continue;
      target[name] = value;
      loaded.push(name);
    }
  }
  return loaded;
}
