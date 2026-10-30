#!/usr/bin/env node
// Production dependency audit (SEC-04) through the registry's bulk advisory
// endpoint, which is what `pnpm audit` uses from pnpm 11 on. pnpm 9 (the
// packageManager pin) posts to the legacy /-/npm/v1/security/audits endpoint,
// which GitHub, the registry's operator, announced it would retire (it still
// answered when this was written). This check does not depend on it. Drop it
// when pnpm is upgraded and `pnpm audit --prod` is back in its place.
//
// The packages are the production tree installed for apps/web
// (`pnpm list --prod --recursive`), so run `pnpm install` first. Optional
// native packages for other platforms are not installed, so they are not
// checked; CI audits the linux x64 tree that Vercel builds.
//
// Any advisory of any severity fails the run, like pip-audit and the former
// `pnpm audit --prod`. An unreachable endpoint or any response that is not an
// advisory map also fails: a check that cannot run is not a pass.
//
//   node scripts/npm-audit-bulk.mjs              audit
//   node scripts/npm-audit-bulk.mjs --self-test  prove advisories and endpoint failures are caught
import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = path.resolve(import.meta.dirname, "..");
const DEFAULT_REGISTRY = "https://registry.npmjs.org/";
const SEMVER = /^\d+\.\d+\.\d+/;

/** name -> sorted versions, from the JSON that `pnpm list --json --depth Infinity` prints. Links and aliases are skipped. */
export function collectPackages(projects) {
  const found = new Map();
  const walk = (dependencies) => {
    for (const [name, info] of Object.entries(dependencies ?? {})) {
      if (typeof info.version === "string" && SEMVER.test(info.version)) {
        if (!found.has(name)) found.set(name, new Set());
        found.get(name).add(info.version);
      }
      walk(info.dependencies);
    }
  };
  for (const project of projects) {
    walk(project.dependencies);
    walk(project.optionalDependencies);
  }
  return Object.fromEntries(
    [...found].sort(([a], [b]) => a.localeCompare(b)).map(([name, v]) => [name, [...v].sort()]),
  );
}

/** POSTs the name -> versions map; returns the advisories by package. Throws unless the answer is an advisory map. */
export async function fetchAdvisories(packages, registry) {
  const url = new URL("-/npm/v1/security/advisories/bulk", registry.endsWith("/") ? registry : `${registry}/`);
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(packages),
    signal: AbortSignal.timeout(60_000),
  });
  if (!response.ok) throw new Error(`the advisory endpoint answered HTTP ${response.status}`);
  const body = await response.json();
  if (body === null || typeof body !== "object" || Array.isArray(body))
    throw new Error("the advisory endpoint answered an unexpected body");
  return body;
}

/** One line per advisory: package, severity, title, and URL. */
export function describe(advisories) {
  const lines = [];
  for (const [name, list] of Object.entries(advisories)) {
    if (!Array.isArray(list)) throw new Error(`the advisory list for ${name} is not a list`);
    for (const a of list)
      lines.push(
        `${name} (${a.vulnerable_versions ?? "?"}): ${a.severity ?? "?"}, ${a.title ?? "advisory"} ${a.url ?? ""}`.trim(),
      );
  }
  return lines;
}

function installedProductionTree() {
  const listed = spawnSync("pnpm", ["list", "--prod", "--recursive", "--depth", "Infinity", "--json"], {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
  });
  if (listed.status !== 0) throw new Error(`pnpm list failed (run \`pnpm install\` first): ${listed.stderr.trim()}`);
  return JSON.parse(listed.stdout);
}

async function selfTest() {
  const failures = [];
  const tree = [
    { name: "root", dependencies: {}, unsavedDependencies: { x: { version: "1.0.0" } } },
    {
      name: "web",
      dependencies: {
        a: { version: "1.2.3", dependencies: { b: { version: "2.0.0", dependencies: { a: { version: "1.2.4" } } } } },
        linked: { version: "link:../x" },
      },
      optionalDependencies: { native: { version: "3.0.0-beta.1" } },
    },
  ];
  const packages = collectPackages(tree);
  const expected = { a: ["1.2.3", "1.2.4"], b: ["2.0.0"], native: ["3.0.0-beta.1"] };
  if (JSON.stringify(packages) !== JSON.stringify(expected)) failures.push(`collected ${JSON.stringify(packages)}`);

  // A local endpoint that answers like the registry: an advisory for b@2.0.0 only, a 410, or nonsense.
  let received;
  let mode = "advisory";
  const server = createServer((request, reply) => {
    let body = "";
    request.on("data", (chunk) => (body += chunk));
    request.on("end", () => {
      received = { method: request.method, url: request.url, body: JSON.parse(body) };
      if (mode === "gone") return reply.writeHead(410, { "content-type": "application/json" }).end("{}");
      reply.writeHead(200, { "content-type": "application/json" });
      if (mode === "nonsense") return reply.end("[]");
      reply.end(
        JSON.stringify(
          mode === "clean"
            ? {}
            : {
                b: [
                  { severity: "high", title: "planted", vulnerable_versions: "<3", url: "https://example.invalid/x" },
                ],
              },
        ),
      );
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const registry = `http://127.0.0.1:${server.address().port}/`;
  try {
    const found = describe(await fetchAdvisories(packages, registry));
    if (found.length !== 1 || !found[0].startsWith("b (<3): high, planted"))
      failures.push(`advisory not reported: ${JSON.stringify(found)}`);
    if (received.method !== "POST" || received.url !== "/-/npm/v1/security/advisories/bulk")
      failures.push(`wrong request ${received.method} ${received.url}`);
    if (JSON.stringify(received.body) !== JSON.stringify(expected))
      failures.push("the package map was not sent as collected");
    mode = "clean";
    if (describe(await fetchAdvisories(packages, registry)).length !== 0)
      failures.push("a clean answer reported advisories");
    for (const bad of ["gone", "nonsense"]) {
      mode = bad;
      const outcome = await fetchAdvisories(packages, registry).then(
        () => "accepted",
        () => "rejected",
      );
      if (outcome !== "rejected") failures.push(`a ${bad} answer was accepted`);
    }
  } finally {
    server.close();
  }
  if (failures.length) {
    console.error(`npm audit self-test FAILED:\n  - ${failures.join("\n  - ")}`);
    process.exit(1);
  }
  console.log("npm audit self-test passed: advisories are reported; a failing or malformed endpoint fails the run.");
}

async function main() {
  if (process.argv.includes("--self-test")) return selfTest();
  const packages = collectPackages(installedProductionTree());
  const count = Object.values(packages).reduce((sum, versions) => sum + versions.length, 0);
  if (count === 0) throw new Error("no production packages found (run `pnpm install` first)");
  const registry = process.env.NPM_CONFIG_REGISTRY || process.env.npm_config_registry || DEFAULT_REGISTRY;
  const found = describe(await fetchAdvisories(packages, registry));
  if (found.length) {
    console.error(`${found.length} advisories in production dependencies:\n  - ${found.join("\n  - ")}`);
    process.exit(1);
  }
  console.log(
    `No known vulnerabilities found (${count} package versions of ${Object.keys(packages).length} packages).`,
  );
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(`npm audit: ${error.message}`);
    process.exit(1);
  });
}
