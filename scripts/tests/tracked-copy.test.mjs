import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { devNull, tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { gitRepositoryProblem, untrackedFilesProblem } from "../lib/tracked-copy.mjs";

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

// `pnpm new:module` leaves the module untracked and edits tracked files to point at it: a copy of the tracked
// files alone then fails far into the run with import errors, so the check names the files first.
test("files git does not track yet are named with the command that adds them; ignored files are not", (t) => {
  const { dir, git } = scratch(t);
  // The caller's global ignore file would decide what counts as untracked.
  const saved = { global: process.env.GIT_CONFIG_GLOBAL, system: process.env.GIT_CONFIG_NOSYSTEM };
  Object.assign(process.env, { GIT_CONFIG_GLOBAL: devNull, GIT_CONFIG_NOSYSTEM: "1" });
  t.after(() => {
    for (const [name, value] of [["GIT_CONFIG_GLOBAL", saved.global], ["GIT_CONFIG_NOSYSTEM", saved.system]]) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  });
  git("init", "-q");
  writeFileSync(path.join(dir, ".gitignore"), "ignored/\n");
  mkdirSync(path.join(dir, "ignored"));
  writeFileSync(path.join(dir, "ignored/scratch.py"), "x = 1\n");
  writeFileSync(path.join(dir, "tracked.py"), "x = 1\n");
  git("add", ".gitignore", "tracked.py");
  assert.equal(untrackedFilesProblem(dir), null, "tracked and ignored files are not a problem");

  const module = "apps/api/app/modules/gadgets/__init__.py";
  mkdirSync(path.dirname(path.join(dir, module)), { recursive: true });
  writeFileSync(path.join(dir, module), "");
  assert.equal(
    untrackedFilesProblem(dir),
    `1 file is not in git yet (${module}): this check copies the files git tracks, so run \`git add -A\` (or delete or ignore it) and run the command again.`,
  );
  for (const name of ["b", "c", "d"]) writeFileSync(path.join(dir, `${name}.py`), "x = 1\n");
  const many = untrackedFilesProblem(dir);
  assert.equal(
    many,
    `4 files are not in git yet (${module}, b.py, c.py, ...): this check copies the files git tracks, so run \`git add -A\` (or delete or ignore them) and run the command again.`,
  );
  assert.ok(!many.includes("\n"));
  git("add", "-A");
  assert.equal(untrackedFilesProblem(dir), null, "staging them is enough");
});

test("test:new-module stops at once, in one line, before it copies anything, when a module is not added to git", (t) => {
  const { dir, env, git } = scratch(t);
  cpSync(path.join(repo, "scripts"), path.join(dir, "scripts"), { recursive: true });
  cpSync(path.join(repo, "package.json"), path.join(dir, "package.json"));
  git("init", "-q");
  git("add", "package.json", "scripts");
  const module = "apps/api/app/modules/gadgets/__init__.py";
  mkdirSync(path.dirname(path.join(dir, module)), { recursive: true });
  writeFileSync(path.join(dir, module), "");
  // Any copy the script made would land here.
  const temporary = path.join(dir, "tmp-for-copies");
  mkdirSync(temporary);
  const run = spawnSync(process.execPath, [path.join(dir, "scripts/test-new-module.mjs")], {
    cwd: dir,
    env: { ...env, TMPDIR: temporary, TMP: temporary, TEMP: temporary },
    encoding: "utf8",
  });
  assert.equal(run.status, 1, `${run.stdout}${run.stderr}`);
  assert.equal(
    run.stderr.trim(),
    `test:new-module: 1 file is not in git yet (${module}): this check copies the files git tracks, so run \`git add -A\` (or delete or ignore it) and run the command again.`,
  );
  assert.equal(run.stdout, "");
  assert.deepEqual(readdirSync(temporary), [], "it copied the repository before stopping");
});
