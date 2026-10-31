import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const repo = path.resolve(import.meta.dirname, "../..");
const read = (file) => readFileSync(path.join(repo, file), "utf8");

const ID = "[A-Z][0-9]+";
const rowPattern = new RegExp(`^\\| (${ID}) `, "gm");
const tagPattern = new RegExp(`\\*\\*\\[YOU (${ID})\\]\\*\\*`, "g");
const idsOf = (text, pattern) => [...text.matchAll(pattern)].map((match) => match[1]);

/**
 * What is wrong with the manual-steps checklist of docs/setup.md (the table rows that start with an ID) and the
 * bold `YOU` tags of the steps in the guides, one line per ID that is not in the checklist once and in the
 * guides once.
 */
function checklistProblems(setup, ...guides) {
  const rows = idsOf(setup, rowPattern);
  const tags = guides.flatMap((text) => idsOf(text, tagPattern));
  const count = (ids, id) => ids.filter((other) => other === id).length;
  return [...new Set([...rows, ...tags])]
    .sort()
    .map((id) => [id, count(rows, id), count(tags, id)])
    .filter(([, inRows, inTags]) => inRows !== 1 || inTags !== 1)
    .map(([id, inRows, inTags]) => `${id}: ${inRows} checklist row(s), ${inTags} **[YOU ${id}]** tag(s); each ID needs one of each`);
}

const setup = read("docs/setup.md");
const deployment = read("docs/deployment.md");
const problems = (changedSetup, changedDeployment = deployment) => checklistProblems(changedSetup, changedSetup, changedDeployment);

test("every step of the manual-steps checklist is tagged in the guide, and every tag has a row", () => {
  assert.ok(idsOf(setup, rowPattern).length > 30, "the checklist rows were not found: the table format changed");
  assert.ok(idsOf(deployment, tagPattern).length > 10, "the tags in docs/deployment.md were not found: the tag format changed");
  assert.deepEqual(problems(setup), []);
});

// The control: each way the checklist and the guide can drift is reported, naming the ID.
test("a renamed, removed, repeated or untagged step is reported", () => {
  const [first] = idsOf(setup, rowPattern);
  const [deployed] = idsOf(deployment, tagPattern);
  const tag = `**[YOU ${first}]**`;
  const wrong = (id, inRows, inTags) => `${id}: ${inRows} checklist row(s), ${inTags} **[YOU ${id}]** tag(s); each ID needs one of each`;

  assert.deepEqual(problems(setup.replace(new RegExp(`^\\| ${first} `, "m"), "| Z99 ")), [wrong(first, 0, 1), wrong("Z99", 1, 0)]);
  assert.deepEqual(problems(setup.replace(tag, "")), [wrong(first, 1, 0)]);
  assert.deepEqual(problems(`${setup}\n${tag}\n`), [wrong(first, 1, 2)]);
  assert.deepEqual(problems(`${setup}\n| ${first} | a second row | none |\n`), [wrong(first, 2, 1)]);
  assert.deepEqual(problems(`${setup}\n**[YOU Z98]**\n`), [wrong("Z98", 0, 1)]);
  assert.deepEqual(problems(setup, deployment.replace(`**[YOU ${deployed}]**`, "")), [wrong(deployed, 1, 0)]);
});
