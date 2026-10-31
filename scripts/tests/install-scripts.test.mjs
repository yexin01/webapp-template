import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

const repo = path.resolve(import.meta.dirname, "../..");
const manifest = JSON.parse(readFileSync(path.join(repo, "package.json"), "utf8"));
const store = path.join(repo, "node_modules", ".pnpm");

// A dependency's install script runs on every machine that installs it, with that machine's keys in reach
// (ADR-0004 §28). pnpm.onlyBuiltDependencies in package.json lists the dependencies allowed to run one; the
// list is explicit, so a new dependency with a script does not run it until it is added here and there.
// To allow one: add its name to both package.json and ALLOWED, with what breaks without it.
const ALLOWED = {};
// Dependencies that declare an install script and run none, with why nothing needs it.
const BLOCKED = {
  "core-js": "posthog-js depends on it; the script only prints a funding notice.",
  esbuild:
    "Reached through the Vercel CLI (tsx, @vercel/node). The script puts the native binary in place of the JavaScript launcher, a speed-up; the package finds the binary of its platform package without it.",
  "unrs-resolver":
    "Reached through eslint-config-next's import resolver. The script checks that the native binding package of the platform is installed, which the lockfile does; `pnpm --filter web lint` passes without it.",
};

/** The `name@version` of every package under a pnpm store that has a preinstall, install or postinstall script, or a binding.gyp. */
function declaredScripts(storeDir) {
  const found = new Set();
  for (const entry of readdirSync(storeDir)) {
    const modules = path.join(storeDir, entry, "node_modules");
    if (!existsSync(modules)) continue;
    for (const name of readdirSync(modules)) {
      for (const pkg of name.startsWith("@") ? readdirSync(path.join(modules, name)).map((n) => `${name}/${n}`) : [name]) {
        let json;
        try {
          json = JSON.parse(readFileSync(path.join(modules, pkg, "package.json"), "utf8"));
        } catch {
          continue;
        }
        if (json.name !== pkg) continue; // a link to a dependency, listed under its own entry
        const hooks = ["preinstall", "install", "postinstall"].some((hook) => json.scripts?.[hook]);
        if (hooks || existsSync(path.join(modules, pkg, "binding.gyp"))) found.add(`${pkg}@${json.version}`);
      }
    }
  }
  return [...found].sort();
}

const nameOf = (id) => id.slice(0, id.lastIndexOf("@"));

test("the allowlist of dependencies that may run an install script is explicit, sorted, and every entry has a reason", () => {
  const allowlist = manifest.pnpm?.onlyBuiltDependencies;
  assert.ok(Array.isArray(allowlist), "pnpm.onlyBuiltDependencies is missing: without it pnpm runs the install script of every dependency");
  assert.deepEqual(allowlist, [...allowlist].sort(), "keep the list sorted");
  assert.deepEqual(allowlist, Object.keys(ALLOWED).sort(), "package.json and ALLOWED in this test list the same names, each with a reason");
  for (const [name, reason] of Object.entries({ ...ALLOWED, ...BLOCKED })) assert.ok(reason.length > 20, `${name} needs its reason`);
});

test("every entry of this test is still in the lockfile", () => {
  const lock = readFileSync(path.join(repo, "pnpm-lock.yaml"), "utf8");
  for (const name of [...Object.keys(ALLOWED), ...Object.keys(BLOCKED)]) {
    assert.ok(new RegExp(`^  '?${name.replaceAll(/[.*+?^${}()|[\]\\/]/g, "\\$&")}@`, "m").test(lock), `${name} is no longer a dependency: remove it from this test`);
  }
});

test("no installed dependency declares an install script that this repository has not decided about", { skip: !existsSync(store) && "dependencies are not installed" }, () => {
  const undecided = declaredScripts(store).filter((id) => !(nameOf(id) in ALLOWED) && !(nameOf(id) in BLOCKED));
  assert.deepEqual(
    undecided,
    [],
    "a dependency declares an install script. If it needs it, add its name to pnpm.onlyBuiltDependencies (package.json) and to ALLOWED in scripts/tests/install-scripts.test.mjs with the reason; otherwise add it to BLOCKED there with why nothing needs it",
  );
});

test("the scan sees a preinstall, install or postinstall script and a binding.gyp, and nothing else", (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), "install-scripts-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const add = (id, pkg, extra = {}) => {
    const folder = path.join(dir, id, "node_modules", pkg);
    mkdirSync(folder, { recursive: true });
    writeFileSync(path.join(folder, "package.json"), JSON.stringify({ name: pkg, version: "1.0.0", ...extra.manifest }));
    if (extra.gyp) writeFileSync(path.join(folder, "binding.gyp"), "{}");
  };
  add("a@1", "a", { manifest: { scripts: { postinstall: "node x.js" } } });
  add("b@1", "@scope/b", { manifest: { scripts: { preinstall: "node x.js" } } });
  add("c@1", "c", { manifest: { scripts: { install: "node x.js" } } });
  add("d@1", "d", { gyp: true });
  add("e@1", "e", { manifest: { scripts: { test: "vitest", build: "tsc", prepare: "husky", postpack: "x" } } });
  assert.deepEqual(declaredScripts(dir), ["@scope/b@1.0.0", "a@1.0.0", "c@1.0.0", "d@1.0.0"]);
});

// The pinned pnpm honors the field: a local dependency whose postinstall writes a file is installed without
// and with the empty allowlist. This is the pnpm on PATH, not Vercel's installer (NOT VERIFIED there).
test("pnpm skips a dependency's install script when the allowlist is empty, and runs it without one", (t) => {
  const pnpm = spawnSync("pnpm", ["--version"], { encoding: "utf8", shell: process.platform === "win32" });
  if (pnpm.status !== 0) return t.skip("pnpm is not installed");
  const dir = mkdtempSync(path.join(tmpdir(), "install-scripts-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const dependency = path.join(dir, "dep");
  mkdirSync(dependency);
  writeFileSync(
    path.join(dependency, "package.json"),
    JSON.stringify({ name: "script-probe-dependency", version: "1.0.0", scripts: { postinstall: "node -e \"require('fs').writeFileSync('ran.txt','')\"" } }),
  );
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !/^npm_/i.test(name)));
  const install = (project, pnpmField) => {
    const folder = path.join(dir, project);
    mkdirSync(folder);
    writeFileSync(
      path.join(folder, "package.json"),
      JSON.stringify({ name: project, version: "1.0.0", private: true, dependencies: { "script-probe-dependency": "file:../dep" }, ...(pnpmField && { pnpm: pnpmField }) }),
    );
    const result = spawnSync("pnpm", ["install", "--offline", "--ignore-workspace", "--no-frozen-lockfile", "--reporter=silent"], { cwd: folder, env, encoding: "utf8", shell: process.platform === "win32" });
    assert.equal(result.status, 0, `${project}: pnpm install failed:\n${result.stdout}${result.stderr}`);
    const built = path.join(folder, "node_modules", ".pnpm", readdirSync(path.join(folder, "node_modules", ".pnpm")).find((name) => name.startsWith("script-probe-dependency")), "node_modules", "script-probe-dependency", "ran.txt");
    return existsSync(built);
  };
  // The control: a pnpm configured to run no dependency script (ignore-scripts) would make the next line pass for nothing.
  if (!install("control", undefined)) return t.skip("this pnpm runs no dependency install scripts, so there is nothing to block");
  assert.equal(install("restricted", { onlyBuiltDependencies: [] }), false, "pnpm ran an install script that the empty allowlist should block");
});
