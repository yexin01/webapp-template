/**
 * Relative links in the repository's Markdown files: finding the ones that do
 * not resolve, and turning the ones that point at removed files into plain text.
 * Only files and folders are checked, not anchors. Links in fenced code blocks
 * and absolute URLs are ignored.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";

const SKIPPED_DIRECTORIES = new Set([
  ".git", ".next", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", "dist",
]);
// Generated files keep their links relative to where they will be copied.
const SKIPPED_PATHS = new Set(["scripts/templates"]);
// [text](target "title"), ![alt](target), and reference definitions: [id]: target
const INLINE = /(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"\n]*")?\s*\)/g;
const DEFINITION = /^\s{0,3}\[[^\]\n]+\]:\s*<?(\S+?)>?(?:\s+"[^"\n]*")?\s*$/;

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

/** Links of `files` whose target does not exist, as `{file, target}`. */
export function brokenLinks(root, files = markdownFiles(root)) {
  const broken = [];
  for (const file of files) {
    const directory = path.dirname(path.join(root, file));
    mapLinks(readFileSync(path.join(root, file), "utf8"), ({ target }) => {
      const resolved = resolveTarget(target, directory, root);
      if (resolved && !existsSync(resolved)) broken.push({ file, target });
      return undefined;
    });
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
