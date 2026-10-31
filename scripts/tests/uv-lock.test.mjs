import assert from "node:assert/strict";
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const repo = path.resolve(import.meta.dirname, "../..");
const scripts = JSON.parse(readFileSync(path.join(repo, "package.json"), "utf8")).scripts;
const hasUv = spawnSync("uv", ["--version"]).status === 0;

/** The arguments of the `uv` command in a package.json script such as "cd apps/api && uv sync --locked". */
const uvArguments = (script) => script.split("&&").map((part) => part.trim()).find((part) => part.startsWith("uv "))?.split(/\s+/).slice(1);

test("bootstrap installs the Python packages through api:sync, and api:sync asserts the lock", () => {
  assert.match(scripts.bootstrap, /\bpnpm api:sync\b/);
  assert.deepEqual(uvArguments(scripts["api:sync"]), ["sync", "--locked"]);
});

// uv.lock records the constraints it was made from. `--frozen` installs it whatever pyproject.toml says now;
// `--locked` fails, so a dependency edit without `uv lock` cannot reach CI, bootstrap or a deployment unnoticed.
test("a uv.lock that pyproject.toml has moved past fails api:sync, which --frozen would let through", { skip: !hasUv && "uv is not installed" }, (t) => {
  const project = mkdtempSync(path.join(tmpdir(), "uv-lock-"));
  t.after(() => rmSync(project, { recursive: true, force: true }));
  for (const file of ["pyproject.toml", "uv.lock", ".python-version"]) copyFileSync(path.join(repo, "apps/api", file), path.join(project, file));
  const pyproject = path.join(project, "pyproject.toml");
  const pin = '"httpx>=0.28,<0.29"';
  const original = readFileSync(pyproject, "utf8");
  assert.ok(original.includes(pin), "the scratch constraint to change is gone: pick another dependency");
  // The copy's lock still satisfies the widened range, but no longer says what pyproject.toml says.
  writeFileSync(pyproject, original.replace(pin, '"httpx>=0.28,<0.30"'));

  // A dry run plans the install and changes nothing, in a scratch environment. The caller's UV_* settings stay out:
  // UV_FROZEN would make uv refuse --locked altogether.
  const env = { ...process.env, UV_PROJECT_ENVIRONMENT: path.join(project, ".venv") };
  for (const name of ["UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC", "UV_OFFLINE"]) delete env[name];
  const uv = (args) => spawnSync("uv", [...args, "--dry-run"], { cwd: project, env, encoding: "utf8" });

  const locked = uv(uvArguments(scripts["api:sync"]));
  if (locked.status !== 0 && !/needs to be updated/.test(locked.stderr)) {
    // A stale lock is only detected by resolving again, which needs uv's cache or the network.
    t.skip(`uv could not decide here: ${locked.stderr.trim().split("\n").at(-1)}`);
    return;
  }
  assert.equal(uv(["sync", "--frozen"]).status, 0, "--frozen refused the stale lock, so this scratch project proves nothing");
  assert.notEqual(locked.status, 0, "api:sync accepted a uv.lock that pyproject.toml has moved past");
  assert.match(locked.stderr, /The lockfile at `uv\.lock` needs to be updated, but `--locked` was provided/);
});
