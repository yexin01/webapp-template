import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { anchorsOf, brokenLinks, citedAnchors, markdownFiles, slugOf, unlinkRemoved } from "../lib/doc-links.mjs";
import { TEMPLATE_ONLY_BLOCK } from "../lib/template.mjs";

function repo(t, files) {
  const root = mkdtempSync(path.join(tmpdir(), "doc-links-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    writeFileSync(path.join(root, rel), text);
  }
  return root;
}

test("brokenLinks reports links to missing files and folders, and nothing else", (t) => {
  const root = repo(t, {
    "README.md": [
      "[ok](docs/a.md) [dir](docs/) [anchor](#top) [web](https://example.com/x.md) [mail](mailto:a@b.c)",
      '[titled](docs/a.md "A title") [with anchor](docs/a.md#section) ![img](docs/missing.png) [gone](docs/gone.md?x=1)',
      "[def]: docs/ref-missing.md",
      "```",
      "[in a fence](docs/never-checked.md)",
      "```",
    ].join("\n"),
    "docs/a.md": "# A\n## Section\n[up](../README.md) [sibling](./b.md)",
    "node_modules/pkg/README.md": "[x](nowhere.md)",
    "scripts/templates/module/README.md": "[x](nowhere.md)",
  });
  assert.deepEqual(markdownFiles(root).sort(), ["README.md", "docs/a.md"]);
  assert.deepEqual(brokenLinks(root), [
    { file: "README.md", target: "docs/missing.png" },
    { file: "README.md", target: "docs/gone.md?x=1" },
    { file: "README.md", target: "docs/ref-missing.md" },
    { file: "docs/a.md", target: "./b.md" },
  ]);
});

test("unlinkRemoved turns links to removed paths into their text and leaves every other link alone", (t) => {
  const root = repo(t, { "docs/ops.md": "x" });
  const removed = [path.join(root, "proofs"), path.join(root, "dev-docs/evidence")];
  const text = [
    "A [proof](../proofs/dec-001/README.md) and [evidence](../dev-docs/evidence/a.md#s \"t\"), ![plot](../proofs/p.png),",
    "[kept](../dev-docs/adr/0001.md), [web](https://example.com/proofs/x), [near](../proofs-other/x.md).",
    "[ref]: ../proofs/ref.md",
    "```",
    "[fenced](../proofs/x.md)",
    "```",
  ].join("\n");
  assert.equal(
    unlinkRemoved(text, "docs/ops.md", root, removed),
    [
      "A proof and evidence, plot,",
      "[kept](../dev-docs/adr/0001.md), [web](https://example.com/proofs/x), [near](../proofs-other/x.md).",
      "",
      "```",
      "[fenced](../proofs/x.md)",
      "```",
    ].join("\n"),
  );
});

test("template-only blocks are removed with their markers and the line break after them", () => {
  const text = "before\n<!-- template-only:start -->\nstatus: pre-release\n<!-- template-only:end -->\nafter\n";
  assert.equal(text.replace(TEMPLATE_ONLY_BLOCK, ""), "before\nafter\n");
  assert.equal("no markers".replace(TEMPLATE_ONLY_BLOCK, ""), "no markers");
});

// GitHub's rules (github-slugger): lowercase; drop everything but letters, marks, numbers, `_`, `-` and spaces;
// spaces become `-`. The text is what the heading renders as, so inline code, links and HTML do not count.
test("slugOf follows GitHub's rules", () => {
  const cases = {
    "Set up Vercel": "set-up-vercel",
    "Part 1: Local, no accounts": "part-1-local-no-accounts",
    "Supabase (optional hosted database)": "supabase-optional-hosted-database",
    "3. Runtime database identity: `app_login`": "3-runtime-database-identity-app_login",
    "`LLM_MODE=disabled` is a supported mode": "llm_modedisabled-is-a-supported-mode",
    "Verify [the first preview](deployment.md#verify) now": "verify-the-first-preview-now",
    "Fast & loose <kbd>Ctrl</kbd>-C": "fast--loose-ctrl-c",
    "A _very_ **bold** heading": "a-very-bold-heading",
    "23. Current Status (2026-10-30)": "23-current-status-2026-10-30",
    "Über-größe": "über-größe",
    "snake_case stays": "snake_case-stays",
  };
  for (const [heading, slug] of Object.entries(cases)) assert.equal(slugOf(heading), slug, heading);
});

test("anchorsOf reads headings outside code fences, numbers repeats, and takes HTML ids", () => {
  const anchors = anchorsOf(
    [
      "# Title",
      "## Setup",
      "text",
      "```bash",
      "# not a heading",
      "```",
      "## Setup ##",
      "### Setup",
      "####### seven hashes are not a heading",
      "#NoSpace is not a heading",
      '<a id="custom"></a> and <a name="legacy">x</a>',
    ].join("\n"),
  );
  assert.deepEqual([...anchors].sort(), ["custom", "legacy", "setup", "setup-1", "setup-2", "title"]);
});

test("brokenLinks checks #fragments against the headings of the target file, and says why", (t) => {
  const root = repo(t, {
    "README.md": [
      "[ok](docs/a.md#the-first-part) [same file ok](#intro) [same file dead](#nowhere) [top](#top) [line](docs/code.mjs#L12)",
      "[dead](docs/a.md#no-such-part) [encoded](docs/a.md#the-first%2Dpart) [spaces](docs/a.md#the%20first%20part) [wrong case](docs/a.md#The-First-Part)",
      "[repeat](docs/a.md#the-first-part-1) [html](docs/a.md#anchor-id) [folder](docs/#x) [web](https://example.com/a.md#nothing)",
      "[query](docs/a.md?plain=1#the-first-part)",
      "```",
      "[in a fence](docs/a.md#never-checked)",
      "```",
      "# Intro",
    ].join("\n"),
    "docs/a.md": '# A\n## The first part\n## The first part\n<a id="anchor-id"></a>\n',
    "docs/code.mjs": "// not Markdown, so a fragment is not checked\n",
  });
  assert.deepEqual(brokenLinks(root, ["README.md"]), [
    { file: "README.md", target: "#nowhere", reason: 'no heading or id "nowhere" in README.md' },
    { file: "README.md", target: "docs/a.md#no-such-part", reason: 'no heading or id "no-such-part" in a.md' },
    { file: "README.md", target: "docs/a.md#the%20first%20part", reason: 'no heading or id "the first part" in a.md' },
    { file: "README.md", target: "docs/a.md#The-First-Part", reason: 'no heading or id "The-First-Part" in a.md' },
  ]);
});

test("a heading that was renamed leaves its links dead, and the pointers in code are checked too", (t) => {
  const root = repo(t, {
    "docs/setup.md": "# Setup\n## Clerk\n",
    "README.md": "[a](docs/setup.md#clerk) [b](docs/setup.md#stripe)",
    ".github/workflows/ci.yml": "# see docs/setup.md#clerk and docs/setup.md#stripe-gone\n",
    "apps/api/app/messages.py": 'HELP = "docs/setup.md#clerk, docs/missing.md#x"\n',
    "apps/api/tests/test_x.py": '"docs/setup.md#made-up-in-a-test"\n',
    "scripts/tests/fixture.test.mjs": "docs/setup.md#made-up-in-a-fixture\n",
    "node_modules/pkg/index.js": "docs/setup.md#vendored\n",
  });
  assert.deepEqual(citedAnchors(root), [
    { file: ".github/workflows/ci.yml", target: "docs/setup.md#stripe-gone", reason: 'no heading or id "stripe-gone" in setup.md' },
  ]);
  assert.deepEqual(
    brokenLinks(root).map(({ file, target }) => `${file}: ${target}`),
    ["README.md: docs/setup.md#stripe", ".github/workflows/ci.yml: docs/setup.md#stripe-gone"],
  );
});

test("the repository's own links and pointers resolve", () => {
  assert.deepEqual(brokenLinks(path.resolve(import.meta.dirname, "../..")), []);
});
