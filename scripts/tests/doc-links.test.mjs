import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { brokenLinks, markdownFiles, unlinkRemoved } from "../lib/doc-links.mjs";
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
    "docs/a.md": "[up](../README.md) [sibling](./b.md)",
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
