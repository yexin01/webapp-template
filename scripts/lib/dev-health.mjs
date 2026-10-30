/**
 * `pnpm dev` starts the API under uvicorn's reloader, which keeps running when
 * its worker dies at import (a configuration error, a port in use): nothing exits,
 * so the only way to notice is to ask the API for its health.
 */
export const HEALTH_URL = "http://127.0.0.1:8000/api/v1/health";

/**
 * Polls `url` until it answers 200, `timeoutMs` pass, or `isStopped()`.
 * @returns {Promise<{ok: true} | {ok: false, reason: "timeout" | "stopped"}>}
 */
export async function waitForHealth({
  url = HEALTH_URL,
  timeoutMs = 60_000,
  intervalMs = 500,
  requestTimeoutMs = 2_000,
  slowAfterMs = 15_000,
  onSlow = () => {},
  isStopped = () => false,
  fetchImpl = fetch,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const start = now();
  let warned = false;
  while (!isStopped()) {
    try {
      const response = await fetchImpl(url, { signal: AbortSignal.timeout(requestTimeoutMs) });
      if (response.ok) return { ok: true };
    } catch {
      // Not listening yet.
    }
    const elapsed = now() - start;
    if (elapsed >= timeoutMs) return { ok: false, reason: "timeout" };
    if (!warned && elapsed >= slowAfterMs) {
      warned = true;
      onSlow();
    }
    await sleep(intervalMs);
  }
  return { ok: false, reason: "stopped" };
}

/** The last line of API output that names an error (a traceback's last line, or uvicorn's `ERROR:`), or null. */
export function lastErrorLine(text) {
  const pattern = /^(?:[A-Za-z_][\w.]*\.)?[A-Za-z_]*(?:Error|Exception|Exit)\b.*$|^ERROR:\s+.+$/;
  const line = text
    .split("\n")
    .map((entry) => entry.trim())
    .findLast((entry) => pattern.test(entry));
  return line ? line.slice(0, 300) : null;
}
