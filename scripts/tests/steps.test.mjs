import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { FAIL, NOT_RUN, PASS, SKIPPED, Tail, annotations, formatDuration, plainText, renderTable, resultCounts, runCommand, runSteps, summarizeOutput } from "../lib/steps.mjs";

const sink = () => {
  const chunks = [];
  return { chunks, write: (chunk) => void chunks.push(String(chunk)) };
};

test("durations read as seconds under a minute, then minutes, then hours", () => {
  assert.equal(formatDuration(4210), "4.2s");
  assert.equal(formatDuration(83_123), "1m23s");
  assert.equal(formatDuration(3_725_000), "1h02m05s");
  assert.equal(formatDuration(-1), "-");
  assert.equal(formatDuration(Number.NaN), "-");
});

test("the tail keeps the last lines of a stream, whatever the size of the pieces", () => {
  const tail = new Tail();
  tail.push("one\ntw");
  tail.push("o\nthree");
  assert.deepEqual(tail.lines(), ["one", "two", "three"]);
  for (let index = 0; index < 500; index += 1) tail.push(`line ${index}\n`);
  assert.equal(tail.lines().length, 120);
  assert.equal(tail.lines().at(-1), "line 499");
});

test("each runner's result line is read from the end of its output", () => {
  assert.equal(
    summarizeOutput("pytest", ["...", "=========== 2 failed, 1441 passed, 5 skipped, 1 warning in 224.31s (0:03:44) ==========="]),
    "2 failed, 1441 passed, 5 skipped, 1 warning in 224.31s (0:03:44)",
  );
  assert.equal(summarizeOutput("pytest", ["============================== 12 passed in 0.51s =============================="]), "12 passed in 0.51s");
  assert.equal(summarizeOutput("pytest", ["Traceback", "boom"]), null);
  assert.equal(summarizeOutput("vitest", [" Test Files  33 passed (33)", "      Tests  514 passed | 1 skipped (515)", "   Duration  12s"]), "514 passed | 1 skipped (515)");
  assert.equal(summarizeOutput("playwright", ["Running 70 tests using 5 workers", "", "  14 skipped", "  56 passed (2.1m)"]), "14 skipped, 56 passed");
  assert.equal(summarizeOutput("playwright", ["  1 failed", "    [desktop] › e2e/a.spec.ts:3:1 › x", "  2 flaky", "  40 passed (52.3s)"]), "1 failed, 2 flaky, 40 passed");
  assert.equal(summarizeOutput("node-test", ["ℹ tests 35", "ℹ suites 0", "ℹ pass 35", "ℹ fail 0"]), "35 tests, 35 pass, 0 fail");
  assert.equal(summarizeOutput("node-test", ["# tests 3", "# pass 2", "# fail 1"]), "3 tests, 2 pass, 1 fail");
  assert.equal(summarizeOutput("unknown", ["x"]), null);
});

test("the outcomes of a pytest or vitest run are counted by word, and a run with no result line has none", () => {
  assert.deepEqual(resultCounts("pytest", ["=========== 2 failed, 1441 passed, 5 skipped, 1 warning in 224.31s (0:03:44) ==========="]), { failed: 2, passed: 1441, skipped: 5 });
  assert.deepEqual(resultCounts("pytest", ["27 passed in 12.34s"]), { passed: 27 });
  assert.deepEqual(resultCounts("pytest", ["27 skipped in 0.21s"]), { skipped: 27 });
  assert.deepEqual(resultCounts("pytest", ["3 passed, 1 xfailed, 2 deselected, 1 error in 1.0s"]), { passed: 3, xfailed: 1, deselected: 2, error: 1 });
  assert.deepEqual(resultCounts("vitest", ["      Tests  19 passed (19)"]), { passed: 19 });
  assert.deepEqual(resultCounts("vitest", ["      Tests  1016 passed | 25 skipped | 1 todo (1042)"]), { passed: 1016, skipped: 25, todo: 1 });
  assert.deepEqual(resultCounts("vitest", ["      Tests  19 skipped (19)"]), { skipped: 19 });
  assert.equal(resultCounts("pytest", ["no tests ran"]), null);
  assert.equal(resultCounts("vitest", ["Error: No test files found"]), null);
});

test("a runner's colors are no part of its result line: vitest and pytest print them in CI", () => {
  // As vitest 5 printed them on a GitHub Actions runner (CI set, no terminal), byte for byte.
  const vitestColored = "\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[32m1096 passed\x1b[39m\x1b[22m\x1b[2m | \x1b[22m\x1b[33m1 skipped\x1b[39m\x1b[90m (1097)\x1b[39m";
  assert.equal(summarizeOutput("vitest", [vitestColored]), "1096 passed | 1 skipped (1097)");
  assert.deepEqual(resultCounts("vitest", [vitestColored]), { passed: 1096, skipped: 1 });
  assert.deepEqual(resultCounts("vitest", ["\x1b[2m      Tests \x1b[22m \x1b[1m\x1b[32m25 passed\x1b[39m\x1b[22m\x1b[90m (25)\x1b[39m"]), { passed: 25 });
  const pytestColored = "\x1b[32m=========== \x1b[32m\x1b[1m27 passed\x1b[0m, \x1b[33m2 skipped\x1b[0m\x1b[32m in 12.34s\x1b[0m\x1b[32m ===========\x1b[0m";
  assert.deepEqual(resultCounts("pytest", [pytestColored]), { passed: 27, skipped: 2 });
  assert.equal(plainText("\x1b[1m\x1b[32mok\x1b[39m\x1b[22m \x1b[2K\x1b[1Adone"), "ok done");
});

test("a table is as wide as its widest cell, with a rule under the header", () => {
  const table = renderTable(["step", "result"], [["api-lint", "PASS"], ["e2e", "FAIL"]]);
  assert.equal(table, ["step      result", "--------  ------", "api-lint  PASS", "e2e       FAIL"].join("\n"));
});

test("workflow commands group a step and annotate its failure on one line", () => {
  assert.equal(annotations.group("API: pytest"), "::group::API: pytest");
  assert.equal(annotations.endGroup(), "::endgroup::");
  assert.equal(annotations.error("API: pytest", "failed: exit 1\nsecond line"), "::error title=API  pytest::failed: exit 1 second line");
});

function fakeClock() {
  let time = 1000;
  return { now: () => time, tick: (ms) => void (time += ms) };
}

const step = (id, code, extra = {}) => ({ id, label: `step ${id}`, run: async () => ({ code, tail: [] }), ...extra });

test("steps run in order and the first failure ends the run: the rest are NOT RUN", async () => {
  const order = [];
  const results = await runSteps(
    [step("a", 0, { run: async () => (order.push("a"), { code: 0, tail: [] }) }), step("b", 2, { run: async () => (order.push("b"), { code: 2, tail: [] }) }), step("c", 0, { run: async () => (order.push("c"), { code: 0, tail: [] }) })],
    { log: () => {} },
  );
  assert.deepEqual(order, ["a", "b"]);
  assert.deepEqual(results.map((result) => [result.id, result.status]), [["a", PASS], ["b", FAIL], ["c", NOT_RUN]]);
  assert.equal(results[1].detail, "exit 2");
  assert.equal(results[2].detail, "an earlier step failed");
});

test("with keepGoing every step runs and each failure is reported", async () => {
  const results = await runSteps([step("a", 1), step("b", 0), step("c", 3)], { keepGoing: true, log: () => {} });
  assert.deepEqual(results.map((result) => result.status), [FAIL, PASS, FAIL]);
});

test("a skipped step is reported with its reason and does not run or stop the run", async () => {
  let ran = false;
  const results = await runSteps(
    [step("a", 0, { skip: "--offline: this step needs the network", run: async () => ((ran = true), { code: 0 }) }), step("b", 0)],
    { log: () => {} },
  );
  assert.equal(ran, false);
  assert.deepEqual(results.map((result) => [result.status, result.detail]), [[SKIPPED, "--offline: this step needs the network"], [PASS, ""]]);
});

test("a step's time comes from the clock around its run, and its summary from the end of its output", async () => {
  const clock = fakeClock();
  const results = await runSteps(
    [
      step("tests", 0, {
        run: async () => (clock.tick(83_000), { code: 0, tail: ["Tests  5 passed (5)"] }),
        summarize: (lines) => summarizeOutput("vitest", lines),
      }),
      step("failing", 1, { run: async () => (clock.tick(2000), { code: 1, tail: ["Tests  1 failed | 4 passed (5)"] }), summarize: (lines) => summarizeOutput("vitest", lines) }),
    ],
    { now: clock.now, log: () => {} },
  );
  assert.deepEqual(results.map((result) => [result.ms, result.detail]), [[83_000, "5 passed (5)"], [2000, "exit 1; 1 failed | 4 passed (5)"]]);
});

test("a command that is not installed, and one killed by a signal, say so", async () => {
  const results = await runSteps(
    [step("a", null, { run: async () => ({ code: null, missing: true, tail: [] }) })],
    { keepGoing: true, log: () => {} },
  );
  assert.equal(results[0].detail, "command not found");
  const killed = await runSteps([step("b", null, { run: async () => ({ code: null, signal: "SIGKILL", tail: [] }) })], { log: () => {} });
  assert.equal(killed[0].detail, "killed by SIGKILL");
});

test("on GitHub Actions each step is a collapsible group and a failure is an annotation", async () => {
  const lines = [];
  await runSteps([step("a", 0), step("b", 1)], { ci: true, log: (line) => lines.push(line) });
  assert.deepEqual(lines, ["::group::step a", "::endgroup::", "::group::step b", "::endgroup::", "::error title=step b::failed: exit 1"]);
});

function fakeChild({ stdout = [], stderr = [], code = 0, error = null }) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  setImmediate(() => {
    if (error) return child.emit("error", error);
    for (const chunk of stdout) child.stdout.emit("data", Buffer.from(chunk));
    for (const chunk of stderr) child.stderr.emit("data", Buffer.from(chunk));
    child.emit("close", code, null);
  });
  return child;
}

test("a command's output is passed through as it arrives and its last lines are kept", async () => {
  const out = sink();
  const err = sink();
  const outcome = await runCommand({
    command: "x",
    args: [],
    stdout: out,
    stderr: err,
    spawnImpl: () => fakeChild({ stdout: ["half a li", "ne\nsecond\n"], stderr: ["warn\n"], code: 4 }),
  });
  assert.equal(outcome.code, 4);
  assert.equal(out.chunks.join(""), "half a line\nsecond\n");
  assert.equal(err.chunks.join(""), "warn\n");
  assert.deepEqual(outcome.tail, ["half a line", "second", "warn"]);
});

test("a command that cannot be started is reported, not thrown", async () => {
  const missing = await runCommand({ command: "x", args: [], spawnImpl: () => fakeChild({ error: Object.assign(new Error("spawn x ENOENT"), { code: "ENOENT" }) }) });
  assert.equal(missing.missing, true);
  assert.equal(missing.code, null);
  const thrown = await runCommand({
    command: "x",
    args: [],
    spawnImpl: () => {
      throw Object.assign(new Error("bad cwd"), { code: "ENOENT" });
    },
  });
  assert.equal(thrown.missing, true);
});

test("a real process: exit status, both streams, and a missing program", async () => {
  const out = sink();
  const err = sink();
  const outcome = await runCommand({
    command: process.execPath,
    args: ["-e", "console.log('to stdout'); console.error('to stderr'); process.exit(3)"],
    env: process.env,
    stdout: out,
    stderr: err,
  });
  assert.equal(outcome.code, 3);
  assert.match(out.chunks.join(""), /to stdout/);
  assert.match(err.chunks.join(""), /to stderr/);
  assert.ok(outcome.tail.includes("to stdout") && outcome.tail.includes("to stderr"));
  const missing = await runCommand({ command: "definitely-not-a-program-verify-test", args: [], env: process.env });
  assert.equal(missing.missing, true);
});
