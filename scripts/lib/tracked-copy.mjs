/**
 * The file set the script tests run on, shared by test:init-template and
 * test:new-module: the repository's tracked files (including staged new
 * files), with their working-tree contents. That is what CI checks out once
 * the changes are committed. Untracked files are left out, so stray scratch
 * files and editor or sync-conflict copies cannot change the result; stage a
 * new file (`git add`) to include it. A check that cannot be right without
 * them stops first and names them (`untrackedFilesProblem`): the generator
 * edits tracked files to point at the files it creates, so a copy without the
 * new files fails with errors that say nothing about the cause.
 *
 * The copy never reaches back into the repository: a tracked symbolic link is
 * copied as the same relative link, and one that is absolute, that leads
 * outside the copy from where it lives there (directly or through other
 * links), or whose target is not in the copy is refused, as is a submodule.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, lstatSync, mkdirSync, mkdtempSync, readlinkSync, realpathSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const list = (repo, args) => execFileSync("git", args, { cwd: repo, encoding: "utf8" }).split("\0").filter(Boolean);

const inside = (base, target) => {
  const rel = path.relative(base, target);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
};

/**
 * Why git cannot list the files of `repo`, in words that say what to do, or null when it can. A copy of the
 * template taken from a ZIP archive has no .git, and git's own "fatal: not a git repository" is no help to
 * someone who has not met git before. `tracked: false` also counts files git does not track yet.
 */
export function gitRepositoryProblem(repo, { tracked = true, env = process.env } = {}) {
  const listed = spawnSync("git", ["ls-files", "-z", "--cached", ...(tracked ? [] : ["--others", "--exclude-standard"])], {
    cwd: repo,
    env,
    encoding: "utf8",
  });
  if (listed.error?.code === "ENOENT") return "this step needs git, which is not installed or not on PATH.";
  if (listed.status !== 0) {
    return `this step needs a git repository, because it works on a copy of the tracked files: run \`git init && git add -A\` in ${repo}, then run it again.`;
  }
  return listed.stdout ? null : `git lists no files in ${repo}: run \`git add -A\`, then run it again.`;
}

/** Ends the run with the problem on one line, no stack trace, when `repo` is not a git repository with files. */
export function requireGitRepository(repo, command) {
  const problem = gitRepositoryProblem(repo);
  if (!problem) return;
  console.error(`${command}: ${problem}`);
  process.exit(1);
}

/** Copies the tracked files of `repo` into a new temporary directory and returns its path. */
export function copyTrackedFiles(repo, prefix) {
  const dir = mkdtempSync(path.join(tmpdir(), prefix));
  try {
    // `<mode> <object> <stage>\t<path>`; a conflicted path is listed once per stage.
    const entries = new Map();
    for (const line of list(repo, ["ls-files", "--stage", "-z"])) {
      const tab = line.indexOf("\t");
      entries.set(line.slice(tab + 1), line.slice(0, tab).split(" ")[0]);
    }
    const links = [];
    for (const [rel, mode] of entries) {
      if (mode === "160000") {
        throw new Error(`${rel} is a git submodule; the script tests copy plain files and cannot include it.`);
      }
      const source = path.join(repo, rel);
      const target = path.join(dir, rel);
      let stat;
      try {
        stat = lstatSync(source);
      } catch {
        continue; // A tracked file deleted in the working tree stays deleted in the copy.
      }
      mkdirSync(path.dirname(target), { recursive: true });
      if (stat.isSymbolicLink()) {
        const link = readlinkSync(source);
        // Checked where the link will live: its `..` climbs from the copy, not from the repository.
        if (path.isAbsolute(link) || !inside(dir, path.resolve(path.dirname(target), link))) {
          throw new Error(`${rel} is a symbolic link to ${link}, outside the repository; the copy would write there.`);
        }
        symlinkSync(link, target);
        links.push(rel);
      } else if (stat.isFile()) {
        cpSync(source, target);
      } else {
        throw new Error(`${rel} is tracked as a file but is not one in the working tree.`);
      }
    }
    // A `..` after another link climbs from that link's target, which only the file system resolves.
    const root = realpathSync.native(dir);
    for (const rel of links) {
      let resolved;
      try {
        resolved = realpathSync.native(path.join(dir, rel));
      } catch (error) {
        const why = error.code === "ENOENT" ? "its target is not in the copy (only tracked files are)" : error.code;
        throw new Error(
          `${rel} is a symbolic link to ${readlinkSync(path.join(dir, rel))} that cannot be resolved: ${why}. Track its target or remove the link.`,
        );
      }
      if (!inside(root, resolved)) {
        throw new Error(`${rel} is a symbolic link that leads outside the repository through another link; the copy would write there.`);
      }
    }
    return dir;
  } catch (error) {
    rmSync(dir, { recursive: true, force: true });
    throw error;
  }
}

/** Untracked, not ignored files that a copy leaves out. */
export function untrackedFiles(repo) {
  return list(repo, ["ls-files", "-z", "--others", "--exclude-standard"]);
}

/** One line that names the files git does not track yet and says what to do, or null when there are none. */
export function untrackedFilesProblem(repo) {
  const files = untrackedFiles(repo);
  if (!files.length) return null;
  const shown = files.slice(0, 3).join(", ") + (files.length > 3 ? ", ..." : "");
  const [which, them] = files.length === 1 ? ["1 file is", "it"] : [`${files.length} files are`, "them"];
  return `${which} not in git yet (${shown}): this check copies the files git tracks, so run \`git add -A\` (or delete or ignore ${them}) and run the command again.`;
}
