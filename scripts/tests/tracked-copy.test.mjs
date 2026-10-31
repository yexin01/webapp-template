import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { devNull, tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { gitRepositoryProblem } from "../lib/tracked-copy.mjs";

const repo = path.resolve(import.meta.dirname, "../..");

/** A folder with no git repository in it or above it, and a git that reads no user configuration. */
function scratch(t) {
  const dir = mkdtempSync(path.join(realpathSync.native(tmpdir()), "tracked-copy-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const env = { ...process.env, GIT_CEILING_DIRECTORIES: path.dirname(dir), GIT_CONFIG_GLOBAL: devNull, GIT_CONFIG_NOSYSTEM: "1" };
  return { dir, env, git: (...args) => execFileSync("git", args, { cwd: dir, env }) };
}

test("a copy without .git is told what to do, in words, not git's fatal message", (t) => {
  const { dir, env } = scratch(t);
  const problem = gitRepositoryProblem(dir, { env });
  assert.match(problem, /^this step needs a git repository, because it works on a copy of the tracked files: run `git init && git add -A` in /);
  assert.ok(problem.includes(dir));
  assert.doesNotMatch(problem, /fatal/);
});

test("a repository with nothing added, or without git at all, is told that too", (t) => {
  const { dir, env, git } = scratch(t);
  git("init", "-q");
  writeFileSync(path.join(dir, "a.txt"), "a\n");
  assert.match(gitRepositoryProblem(dir, { env }), /^git lists no files in .*: run `git add -A`, then run it again\.$/);
  assert.equal(gitRepositoryProblem(dir, { env, tracked: false }), null, "an untracked file counts when the step takes those too");
  git("add", "a.txt");
  assert.equal(gitRepositoryProblem(dir, { env }), null);
  assert.match(gitRepositoryProblem(dir, { env: { ...env, PATH: "" } }), /^this step needs git, which is not installed or not on PATH\.$/);
});

// The scripts that copy the tracked files, run from a ZIP copy of the template (no .git): one line, no stack trace.
test("test:init-template, test:new-module and test:vercel-build end with the instruction in a copy without .git", (t) => {
  const { dir, env } = scratch(t);
  cpSync(path.join(repo, "scripts"), path.join(dir, "scripts"), { recursive: true });
  cpSync(path.join(repo, "package.json"), path.join(dir, "package.json"));
  mkdirSync(path.join(dir, "node_modules/.bin"), { recursive: true });
  writeFileSync(path.join(dir, "node_modules/.bin/vercel"), ""); // present, so the check that comes first passes
  for (const script of ["test-init-template", "test-new-module", "test-vercel-build"]) {
    const run = spawnSync(process.execPath, [path.join(dir, "scripts", `${script}.mjs`)], { cwd: dir, env, encoding: "utf8" });
    assert.equal(run.status, 1, `${script}: ${run.stderr}`);
    assert.match(run.stderr, /this step needs a git repository, because it works on a copy of the tracked files: run `git init && git add -A`/, script);
    assert.doesNotMatch(run.stderr, /\n\s+at |node:internal|fatal:/, `${script} printed a stack trace or git's message:\n${run.stderr}`);
    assert.ok(run.stderr.trim().split("\n").length <= 2, `${script} printed more than a line:\n${run.stderr}`);
  }
});
