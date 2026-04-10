import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");

const packageJsonPath = path.join(repoRoot, "package.json");
const manifestPath = path.join(repoRoot, "custom_components", "marstek", "manifest.json");
const pyprojectPath = path.join(repoRoot, "pyproject.toml");

const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"));
const nextVersion = packageJson.version;

if (typeof nextVersion !== "string" || nextVersion.length === 0) {
  throw new Error("package.json must define a non-empty version string");
}

const manifestContents = await readFile(manifestPath, "utf8");
const manifestVersionPattern = /^  "version": ".*"$/m;

if (!manifestVersionPattern.test(manifestContents)) {
  throw new Error("Could not find an integration version in manifest.json");
}

const nextManifestContents = manifestContents.replace(
  manifestVersionPattern,
  `  "version": "${nextVersion}"`,
);

await writeFile(manifestPath, nextManifestContents, "utf8");

const pyprojectContents = await readFile(pyprojectPath, "utf8");
const pyprojectVersionPattern = /^version = ".*"$/m;

if (!pyprojectVersionPattern.test(pyprojectContents)) {
  throw new Error("Could not find a project version in pyproject.toml");
}

const nextPyprojectContents = pyprojectContents.replace(
  pyprojectVersionPattern,
  `version = "${nextVersion}"`,
);

await writeFile(pyprojectPath, nextPyprojectContents, "utf8");

console.log(`Synced release version ${nextVersion} to manifest.json and pyproject.toml`);