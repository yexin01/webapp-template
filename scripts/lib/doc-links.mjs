/**
 * Relative links in the repository's Markdown files: finding the ones that do
 * not resolve, and turning the ones that point at removed files into plain text.
 * A link must name a file or folder that exists and, when it ends in `#fragment`,
 * a heading (by GitHub's slug rules) or an HTML id of that Markdown file. The
 * `docs/x.md#fragment` pointers that code and workflows print or cite are checked
 * the same way. Links in fenced code blocks and absolute URLs are ignored.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const SKIPPED_DIRECTORIES = new Set([
  ".git", ".next", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", "dist",
]);
// Generated files keep their links relative to where they will be copied.
const SKIPPED_PATHS = new Set(["scripts/templates"]);
// [text](target "title"), ![alt](target), and reference definitions: [id]: target
const INLINE = /(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"\n]*")?\s*\)/g;
const DEFINITION = /^\s{0,3}\[[^\]\n]+\]:\s*<?(\S+?)>?(?:\s+"[^"\n]*")?\s*$/;
// Where code names a documentation anchor: the files that print or cite one, not the tests that use made-up ones.
const CITING_EXTENSIONS = new Set([".py", ".ts", ".tsx", ".mjs", ".js", ".yml", ".yaml", ".sh", ".tmpl", ".toml", ".example"]);
const CITATION = /\bdocs\/([A-Za-z0-9_-]+\.md)#([A-Za-z0-9_%-]+)/g;
const NOT_CITING = /(^|\/)(tests?|e2e)\/|\.(test|spec|testing)\.[a-z]+$/;

/** Every Markdown file under `root` that holds links of its own, as repository-relative paths. */
export function markdownFiles(root, directory = root) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    const rel = path.relative(root, full).split(path.sep).join("/");
    if (entry.isDirectory()) return SKIPPED_DIRECTORIES.has(entry.name) || SKIPPED_PATHS.has(rel) ? [] : markdownFiles(root, full);
    return entry.isFile() && entry.name.endsWith(".md") ? [rel] : [];
  });
}

/** The file or folder a link target names, relative to `fileDirectory`, or null for a URL, an anchor, or a placeholder. */
export function resolveTarget(target, fileDirectory, root) {
  if (!target || target.startsWith("#") || target.startsWith("<") || target.startsWith("{{")) return null;
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(target) || target.startsWith("//")) return null;
  let clean = target.split("#")[0].split("?")[0];
  try {
    clean = decodeURIComponent(clean);
  } catch {
    // Left as written.
  }
  if (!clean) return null;
  return clean.startsWith("/") ? path.join(root, clean) : path.resolve(fileDirectory, clean);
}

/** The text a heading renders as: no markup, no link targets, no HTML. */
function plainHeading(source) {
  return source
    .replace(/<[^>]*>/g, "")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/!?\[([^\]]*)\]\[[^\]]*\]/g, "$1")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/(^|[\s(])_([^_\s](?:[^_]*[^_\s])?)_(?=[\s).,:;!?]|$)/g, "$1$2")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

/** GitHub's anchor for a heading: lowercase, only letters, marks, numbers, `_`, `-` and spaces, spaces as `-`. */
export function slugOf(heading) {
  return plainHeading(heading)
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, "")
    .replaceAll(" ", "-");
}

/**
 * The anchors a Markdown file offers: its ATX headings outside fenced code blocks, a repeated one numbered
 * `-1`, `-2` as GitHub does, and the `id` and `name` of its HTML elements. Setext headings (underlined) are
 * not read: the repository does not use them.
 */
export function anchorsOf(text) {
  const anchors = new Set();
  const occurrences = new Map(); // as github-slugger: every slug handed out, and how often its base repeated
  let fenced = false;
  for (const line of text.split("\n")) {
    if (/^\s{0,3}(```|~~~)/.test(line)) fenced = !fenced;
    if (fenced) continue;
    const heading = /^ {0,3}#{1,6}(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$/.exec(line);
    if (heading) {
      const base = slugOf(heading[1] ?? "");
      let slug = base;
      while (occurrences.has(slug)) {
        occurrences.set(base, occurrences.get(base) + 1);
        slug = `${base}-${occurrences.get(base)}`;
      }
      occurrences.set(slug, 0);
      anchors.add(slug);
    }
    for (const [, , id] of line.matchAll(/<[A-Za-z][^>]*?\s(id|name)\s*=\s*["']([^"']+)["']/g)) anchors.add(id);
  }
  return anchors;
}

/** Runs `visit(match)` for every link outside fenced code blocks; a visitor returning text replaces the link. */
function mapLinks(text, visit) {
  let fenced = false;
  return text
    .split("\n")
    .map((line) => {
      if (/^\s{0,3}(```|~~~)/.test(line)) fenced = !fenced;
      if (fenced) return line;
      const definition = DEFINITION.exec(line);
      if (definition) return visit({ kind: "definition", target: definition[1], whole: line, text: "" }) ?? line;
      return line.replace(INLINE, (whole, bang, label, target) => visit({ kind: bang ? "image" : "link", target, whole, text: label }) ?? whole);
    })
    .join("\n");
}

/** Why the fragment of a link or citation does not resolve in `markdownFile`, or null when it does (or is not checked). */
function missingAnchor(fragment, markdownFile, cache) {
  let decoded = fragment;
  try {
    decoded = decodeURIComponent(fragment);
  } catch {
    // Left as written.
  }
  // `#top` scrolls to the top of any page; `#L12` and `#L12-L20` are line anchors of GitHub's file view.
  if (!decoded || decoded === "top" || /^L\d+(?:-L\d+)?$/.test(decoded)) return null;
  if (!cache.has(markdownFile)) cache.set(markdownFile, anchorsOf(readFileSync(markdownFile, "utf8")));
  return cache.get(markdownFile).has(decoded) ? null : `no heading or id "${decoded}" in ${path.basename(markdownFile)}`;
}

const isMarkdownFile = (file) => file.endsWith(".md") && existsSync(file) && statSync(file).isFile();

/**
 * Links of `files` that do not resolve, as `{file, target}`, with a `reason` when the file exists and the
 * `#fragment` does not: a missing file or folder, or a missing heading or id. With no `files`, every Markdown
 * file, and the `docs/x.md#fragment` pointers in code (see `citedAnchors`).
 */
export function brokenLinks(root, files) {
  const cache = new Map();
  const broken = [];
  for (const file of files ?? markdownFiles(root)) {
    const self = path.join(root, file);
    const directory = path.dirname(self);
    mapLinks(readFileSync(self, "utf8"), ({ target }) => {
      if (!target || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(target) || target.startsWith("//") || target.startsWith("<") || target.startsWith("{{")) {
        return undefined;
      }
      const resolved = resolveTarget(target, directory, root);
      const fragment = target.includes("#") ? target.slice(target.indexOf("#") + 1) : null;
      if (resolved && !existsSync(resolved)) broken.push({ file, target });
      else if (fragment !== null) {
        const where = resolved ?? self; // a bare `#fragment` is a heading of the file that holds the link
        const reason = isMarkdownFile(where) ? missingAnchor(fragment.split("?")[0], where, cache) : null;
        if (reason) broken.push({ file, target, reason });
      }
      return undefined;
    });
  }
  return files ? broken : [...broken, ...citedAnchors(root, cache)];
}

/** Every file under `directory` that can cite a documentation anchor, as repository-relative paths. */
function citingFiles(root, directory = root) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(directory, entry.name);
    const rel = path.relative(root, full).split(path.sep).join("/");
    if (entry.isDirectory()) return SKIPPED_DIRECTORIES.has(entry.name) || SKIPPED_PATHS.has(rel) || NOT_CITING.test(`${rel}/`) ? [] : citingFiles(root, full);
    const extension = path.extname(entry.name);
    return entry.isFile() && CITING_EXTENSIONS.has(extension) && !NOT_CITING.test(rel) && !rel.startsWith("docs-notes/") && !rel.startsWith("dev-docs/evidence/") ? [rel] : [];
  });
}

/**
 * The `docs/x.md#fragment` pointers in code, workflows and scripts (an error message that says where to read,
 * a comment) that name a heading `x.md` does not have, as `{file, target, reason}`. A pointer to a document
 * this checkout does not have is the business of `brokenLinks`, not of this.
 */
export function citedAnchors(root, cache = new Map()) {
  const broken = [];
  for (const file of citingFiles(root)) {
    for (const [, name, fragment] of readFileSync(path.join(root, file), "utf8").matchAll(CITATION)) {
      const document = path.join(root, "docs", name);
      const reason = isMarkdownFile(document) ? missingAnchor(fragment, document, cache) : null;
      if (reason) broken.push({ file, target: `docs/${name}#${fragment}`, reason });
    }
  }
  return broken;
}

/**
 * `text` (the contents of `file`) with each link to a path under one of `removed`
 * (absolute paths) replaced by its text; a reference definition is dropped.
 */
export function unlinkRemoved(text, file, root, removed) {
  const directory = path.dirname(path.join(root, file));
  const gone = (resolved) => resolved && removed.some((prefix) => resolved === prefix || resolved.startsWith(prefix + path.sep));
  return mapLinks(text, ({ kind, target, text: label }) => {
    if (!gone(resolveTarget(target, directory, root))) return undefined;
    return kind === "definition" ? "" : label;
  });
}
