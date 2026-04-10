---
name: changesets
description: How to create, update, and manage Changesets for release preparation in this repository
---

# Changesets

This skill covers how the repository uses [Changesets](https://github.com/changesets/changesets) to track releasable changes, generate changelogs, and keep version numbers in sync across `package.json`, `custom_components/marstek/manifest.json`, and `pyproject.toml`.

## When to Use

Use this skill whenever you add, modify, or remove code, configuration, documentation, or any other file that should be noted in the next release. A changeset is the mechanism that records *what changed* and *how it affects the version*.

## Quick Reference

| Command | Purpose |
|---------|---------|
| `npm run changeset` | Interactively create a new changeset |
| `npm run changeset:version` | Consume pending changesets, bump versions, update changelog |
| `npm run changeset:pre:enter` | Enter RC prerelease mode (`-rc.N` suffixes) |
| `npm run changeset:pre:exit` | Mark prerelease mode for exit on the next version run |
| `npm run release:tag` | Create and push the `v*` git tag (used by CI) |

## What Is a Changeset

A changeset is a Markdown file in `.changeset/` with YAML frontmatter declaring the bump type and a human-readable summary that becomes the changelog entry.

```markdown
---
"ha-marstek-release-tools": minor
---

Add PV channel 3 and 4 sensors for Venus D devices.
```

The package name is always `"ha-marstek-release-tools"` (the `name` in `package.json`).

## Bump Types

| Type | When to Use | Example |
|------|-------------|---------|
| `patch` | Bug fixes, internal refactors, documentation, maintenance | Fix battery status showing wrong state |
| `minor` | New features, new entities, new services (backward-compatible) | Add PV sensors for Venus A |
| `major` | Breaking changes (config migration, removed entities, API changes) | Remove deprecated YAML config support |

When multiple changesets accumulate before a release, Changesets picks the **highest** bump among them.

## Creating a Changeset

### Interactively

```bash
npm run changeset
```

Follow the prompts: select the package, choose the bump type, write a summary.

### Manually

Create a file in `.changeset/` with any `kebab-case-name.md`:

```markdown
---
"ha-marstek-release-tools": patch
---

Fix passive mode timer not expiring after configured duration.
```

Commit the file alongside the code change.

## Writing Good Changeset Summaries

- **One sentence, user-facing language.** Describe what the user will notice, not the implementation detail.
- **Start with a verb.** "Add …", "Fix …", "Remove …", "Change …".
- **Be specific.** "Fix battery power showing 0 W during idle" is better than "Fix sensor bug".
- **Group related changes.** One changeset per logical change; a single PR may have one or multiple changesets.

### Examples

| Good | Bad |
|------|-----|
| Add CT connection binary sensor | Added new file binary_sensor.py |
| Fix grid power reading on 3-phase setups | Bugfix |
| Remove deprecated `marstek.force_refresh` service | Cleanup |

## When NOT to Create a Changeset

- Purely editorial fixes to comments or docstrings that don't affect behavior.
- Changes that only touch CI workflows, dev tooling, or test infrastructure with no user-visible impact.
- The automated **Release** PR created by the Changesets GitHub Action.

## How Versioning Works

1. Contributors add `.changeset/*.md` files with their PRs.
2. On merge to `main`, the **Changesets** GitHub Action opens or updates a **Release** PR.
3. That PR runs `npm run changeset:version`, which:
   - Consumes all pending changeset files (deletes them).
   - Bumps `package.json` version.
   - Runs `scripts/sync-release-version.mjs` to sync the version into `custom_components/marstek/manifest.json` and `pyproject.toml`.
   - Appends entries to `CHANGELOG.md`.
4. When the Release PR is merged, `scripts/create-release-tag.mjs` creates and pushes the `v*` tag.
5. The existing `release.yaml` workflow picks up the tag and creates the GitHub Release.

## Prerelease (RC) Mode

For release-candidate trains, enter prerelease mode **before** merging the next batch:

```bash
npm run changeset:pre:enter   # creates .changeset/pre.json
```

While active, `changeset version` produces versions like `1.1.0-rc.0`, `1.1.0-rc.1`, etc.

When ready to cut the stable release:

```bash
npm run changeset:pre:exit    # marks intent to leave pre mode
# Then run changeset:version or let the Release PR do it
```

Commit `pre.json` changes. See the [official prerelease docs](https://github.com/changesets/changesets/blob/main/docs/prereleases.md) for edge cases.

## Files Involved

| File | Role |
|------|------|
| `.changeset/config.json` | Changesets configuration (base branch, changelog format) |
| `.changeset/*.md` | Pending changeset files (consumed on version) |
| `.changeset/pre.json` | Prerelease state (only present in RC mode) |
| `package.json` | Source of truth for the current version |
| `scripts/sync-release-version.mjs` | Syncs version to manifest.json and pyproject.toml |
| `scripts/create-release-tag.mjs` | Creates and pushes the git tag after release merge |
| `.github/workflows/changesets.yaml` | Opens/updates the Release PR on main |
| `.github/workflows/changeset-check.yaml` | Warns on PRs missing a changeset |
| `.github/workflows/release.yaml` | Creates GitHub Release from pushed tags |
