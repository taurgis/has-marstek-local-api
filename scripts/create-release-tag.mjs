import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const packageJsonPath = path.join(repoRoot, "package.json");

const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"));
const version = packageJson.version;

if (typeof version !== "string" || version.length === 0) {
  throw new Error("package.json must define a non-empty version string");
}

const tagName = `v${version}`;

const runGit = async (...args) =>
  execFileAsync("git", args, {
    cwd: repoRoot,
  });

try {
  await runGit("rev-parse", "--verify", `refs/tags/${tagName}`);
  console.log(`Tag ${tagName} already exists; skipping.`);
  process.exit(0);
} catch {
  // Continue and create the tag.
}

await runGit("tag", "-a", tagName, "-m", `Release ${tagName}`);
await runGit("push", "origin", `refs/tags/${tagName}`);

console.log(`Created and pushed ${tagName}`);