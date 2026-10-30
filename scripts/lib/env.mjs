/**
 * The root .env as the scripts read it. The API reads .env and lets the
 * process environment win (pydantic-settings); the scripts decide the same way.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";

/** The variables of the root .env, or null when there is no such file. */
export function readDotenv(root) {
  try {
    return { ...parseEnv(readFileSync(path.join(root, ".env"), "utf8")) };
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

/** What the API sees: the process environment over the root .env. */
export function effectiveEnv(root, processEnv = process.env) {
  const defined = Object.entries(processEnv).filter(([, value]) => value !== undefined);
  return { ...(readDotenv(root) ?? {}), ...Object.fromEntries(defined) };
}
