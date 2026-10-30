/**
 * Running a list of commands and reporting on them: shared by `pnpm verify` and `pnpm verify:sandbox`.
 * Nothing here knows which commands matter; the callers own the lists. Process spawning, the clock and the
 * output streams are parameters, so the orchestration is tested without running anything.
 */
import { spawn } from "node:child_process";

export const PASS = "PASS";
export const FAIL = "FAIL";
export const SKIPPED = "SKIPPED";
export const NOT_RUN = "NOT RUN";

const TAIL_LINES = 120;

/** 83123 -> "1m23s", 4210 -> "4.2s". */
export function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return "-";
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const total = Math.round(ms / 1000);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return hours ? `${hours}h${String(minutes).padStart(2, "0")}m${String(seconds).padStart(2, "0")}s` : `${minutes}m${String(seconds).padStart(2, "0")}s`;
}

/** Keeps the last lines of a stream as it arrives, in pieces of any size. */
export class Tail {
  #lines = [];
  #partial = "";
  push(chunk) {
    const parts = (this.#partial + chunk).split(/\r?\n/);
    this.#partial = parts.pop() ?? "";
    this.#lines.push(...parts);
    if (this.#lines.length > TAIL_LINES) this.#lines.splice(0, this.#lines.length - TAIL_LINES);
  }
  lines() {
    return this.#partial ? [...this.#lines, this.#partial] : [...this.#lines];
  }
}

/**
 * Runs one command, streaming its output, and resolves (never rejects) with how it ended.
 * @returns {Promise<{code: number | null, signal: string | null, missing: boolean, tail: string[]}>}
 */
export function runCommand({ command, args, cwd, env, stdout = process.stdout, stderr = process.stderr, spawnImpl = spawn }) {
  return new Promise((resolve) => {
    const tail = new Tail();
    let child;
    try {
      child = spawnImpl(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    } catch (error) {
      resolve({ code: null, signal: null, missing: error.code === "ENOENT", tail: [String(error.message)] });
      return;
    }
    const forward = (stream) => (chunk) => {
      stream.write(chunk);
      tail.push(chunk.toString());
    };
    child.stdout?.on("data", forward(stdout));
    child.stderr?.on("data", forward(stderr));
    child.on("error", (error) => resolve({ code: null, signal: null, missing: error.code === "ENOENT", tail: [...tail.lines(), String(error.message)] }));
    child.on("close", (code, signal) => resolve({ code, signal, missing: false, tail: tail.lines() }));
  });
}

/**
 * The result line of a test runner, from the end of its output, or null when it printed none.
 * pytest: "== 2 failed, 1441 passed, 5 skipped in 224.31s (0:03:44) ==";
 * vitest: "Tests  514 passed | 1 skipped (515)"; Playwright: "40 passed (52.3s)" with "20 skipped" above it;
 * node --test: "# tests 35" with "# pass 35" and "# fail 0".
 */
export function summarizeOutput(kind, lines) {
  const last = (pattern) => lines.findLast((line) => pattern.test(line));
  switch (kind) {
    case "pytest": {
      const line = last(/\b\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed)\b.* in [\d.]+s/);
      return line ? line.replace(/^[=\s]+|[=\s]+$/g, "") : null;
    }
    case "vitest": {
      const line = last(/^\s*Tests\s+\d+/);
      return line ? line.trim().replace(/^Tests\s+/, "") : null;
    }
    case "playwright": {
      const counts = lines
        .filter((line) => /^\s+\d+ (?:passed|failed|skipped|flaky|interrupted|did not run)\b/.test(line))
        .map((line) => line.trim().replace(/\s+\([\d.]+(?:ms|s|m|h)\)$/, ""));
      return counts.length ? counts.join(", ") : null;
    }
    case "node-test": {
      const pick = (name) => lines.findLast((line) => new RegExp(`^(?:#|ℹ)\\s+${name}\\s+\\d+`).test(line))?.match(/(\d+)\s*$/)?.[1];
      const [tests, pass, fail] = ["tests", "pass", "fail"].map(pick);
      return tests === undefined ? null : `${tests} tests, ${pass ?? "?"} pass, ${fail ?? "?"} fail`;
    }
    default:
      return null;
  }
}

/** A table of rows of strings, left aligned, columns as wide as their widest cell. */
export function renderTable(header, rows) {
  const widths = header.map((cell, index) => Math.max(cell.length, ...rows.map((row) => String(row[index] ?? "").length)));
  const line = (row) => row.map((cell, index) => String(cell ?? "").padEnd(widths[index])).join("  ").trimEnd();
  return [line(header), line(widths.map((width) => "-".repeat(width))), ...rows.map(line)].join("\n");
}

/** GitHub Actions workflow commands: collapsible groups and an error annotation. */
export const annotations = {
  group: (title) => `::group::${title}`,
  endGroup: () => "::endgroup::",
  error: (title, message) => `::error title=${title.replaceAll(/[:,\r\n]/g, " ")}::${message.replaceAll(/\r?\n/g, " ")}`,
};

/**
 * Runs steps in order. A step carries `skip` (a reason it does not run; it is reported SKIPPED) or runs `run()`.
 * Without `keepGoing`, the first failure ends the run and the rest are reported NOT RUN.
 * @param {Array<{id: string, label: string, skip?: string, run: () => Promise<{code: number | null, signal?: string | null, missing?: boolean, tail?: string[]}>, summarize?: (tail: string[]) => string | null}>} steps
 * @returns {Promise<Array<{id: string, label: string, status: string, ms: number, detail: string}>>}
 */
export async function runSteps(steps, { keepGoing = false, now = Date.now, log = console.log, ci = false } = {}) {
  const results = [];
  let failed = false;
  for (const step of steps) {
    if (failed && !keepGoing) {
      results.push({ id: step.id, label: step.label, status: NOT_RUN, ms: 0, detail: "an earlier step failed" });
      continue;
    }
    if (step.skip) {
      log(`verify: ${step.label}: SKIPPED (${step.skip})`);
      results.push({ id: step.id, label: step.label, status: SKIPPED, ms: 0, detail: step.skip });
      continue;
    }
    log(ci ? annotations.group(step.label) : `\n=== ${step.label} ===`);
    const start = now();
    const outcome = await step.run();
    const ms = now() - start;
    const ok = outcome.code === 0;
    const detail = ok
      ? (step.summarize?.(outcome.tail ?? []) ?? "")
      : outcome.missing
        ? "command not found"
        : outcome.signal
          ? `killed by ${outcome.signal}`
          : `exit ${outcome.code}${step.summarize?.(outcome.tail ?? []) ? `; ${step.summarize(outcome.tail ?? [])}` : ""}`;
    if (ci) log(annotations.endGroup());
    if (!ok) {
      failed = true;
      log(ci ? annotations.error(step.label, `failed: ${detail}`) : `verify: ${step.label} FAILED (${detail})`);
    }
    results.push({ id: step.id, label: step.label, status: ok ? PASS : FAIL, ms, detail });
  }
  return results;
}
