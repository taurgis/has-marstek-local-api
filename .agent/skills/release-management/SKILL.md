```skill
---
name: release-management
description: REQUIRED for any release work. Use this skill for version bumps, changelog updates, tagging, and publishing for the Marstek integration.
---

# Release Management

This skill covers the complete release workflow for the Marstek Home Assistant integration.

## When to Use (Required)

Use this skill for any release request (RC, stable, patch, minor, or major). Do not skip it when preparing a release, updating versions, or publishing tags/releases.

## Release Types

| Type | Version Format | Example | Use Case |
|------|---------------|---------|----------|
| Release Candidate | `X.Y.Z-rcN` | `1.0.0-rc2` | Pre-release testing |
| Stable | `X.Y.Z` | `1.0.0` | Production release |
| Patch | `X.Y.Z` | `1.0.1` | Bug fixes |
| Minor | `X.Y.0` | `1.1.0` | New features (backward compatible) |
| Major | `X.0.0` | `2.0.0` | Breaking changes |

## External Requirements (Official Docs)

- Home Assistant requires a valid integration `version` in the manifest, using a SemVer-compatible format. https://developers.home-assistant.io/docs/creating_integration_manifest/#version
- SemVer supports prerelease identifiers like `1.0.0-rc.1`, and prereleases sort lower than the associated stable version. https://semver.org/
- HACS uses the tag name from the latest GitHub release as the remote version; tags alone are not enough. https://hacs.xyz/docs/publish/start/#versions
- GitHub releases can be marked as pre-releases for RCs. https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository#creating-a-release

## Release Checklist

### 1. Pre-Release Validation

```bash
# Linting (code style and common errors)
python3 -m ruff check custom_components/marstek/

# Type checking (strict mode required)
python3 -m mypy --strict custom_components/marstek/

# Run all tests with coverage gate
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95

# Both MUST pass before proceeding
```

### 2. Generate Changelog from Git

Get commits since the last tag:

```bash
# List all tags (newest first)
git tag -l --sort=-v:refname

# Get commits since last tag
git log --oneline <LAST_TAG>..HEAD
```

#### Changelog Entry Format

```markdown
## [VERSION] - YYYY-MM-DD

### Added
- New feature descriptions

### Changed
- Behavior changes

### Fixed
- Bug fixes

### Maintenance
- Internal improvements, refactoring, documentation
```

#### Commit Prefix Mapping

| Prefix | Changelog Section |
|--------|-------------------|
| `New feature:` | Added |
| `Feature:` | Added |
| `Maintenance:` | Maintenance |
| `Fix:` | Fixed |
| `Bugfix:` | Fixed |
| `Documentation:` | Maintenance |
| `Testing:` | Maintenance |
| `Refactor:` | Changed |

### 3. Update Version Numbers

Two files must be updated:

#### manifest.json

```json
{
  "version": "X.Y.Z-rcN"
}
```

Location: `custom_components/marstek/manifest.json`

Note: The manifest `version` must stay aligned with the GitHub release tag (HACS uses the release tag as the remote version).

#### pyproject.toml (optional)

If version is tracked there, update it as well.

### 4. Update CHANGELOG.md

Add new section at the top (below the header):

```markdown
# Changelog

All notable changes to this project will be documented in this file.

## [NEW_VERSION] - YYYY-MM-DD

### Section
- Entry from git log

## [PREVIOUS_VERSION] - YYYY-MM-DD
...
```

### 5. Commit and Tag

```bash
# Stage changes
git add CHANGELOG.md custom_components/marstek/manifest.json

# Commit with version in message
git commit -m "Release vX.Y.Z-rcN"

# Create annotated tag
git tag -a vX.Y.Z-rcN -m "Release vX.Y.Z-rcN"

# Push with tags
git push origin main --tags
```

### 6. Post-Release Verification

- Verify tag appears on GitHub
- **GitHub Action automatically creates the release** (see below)
- Check HACS can detect the new version
- Validate HACS manifest requirements if needed

## Automated Release via GitHub Actions

A GitHub Action (`.github/workflows/release.yaml`) automatically creates releases when tags are pushed:

### What it does

1. Triggers on any tag matching `v*`
2. Extracts the version number from the tag
3. Parses `CHANGELOG.md` to get release notes for that version
4. Determines if it's a pre-release (contains `-rc`, `-beta`, `-alpha`)
5. Creates a GitHub Release with the extracted notes

### Workflow

```
git push origin main --tags
        ↓
GitHub detects new tag (v1.0.0-rc2)
        ↓
release.yaml workflow runs
        ↓
GitHub Release created automatically
```

### Manual release (if needed)

If the action fails or you need to recreate:

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes-file release_notes.md --prerelease
```

## Quick Reference Commands

```bash
# View all tags
git tag -l --sort=-v:refname

# View commits between tags
git log --oneline v1.0.0-rc1..v1.0.0-rc2

# Delete a tag (local + remote) if needed
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z

# Amend last commit and re-tag
git tag -d vX.Y.Z
git commit --amend --no-edit
git tag -a vX.Y.Z -m "Release vX.Y.Z"
```

## Release Candidate Workflow

For RC releases:

1. Increment RC number: `rc1` → `rc2` → `rc3`
2. When stable, drop `-rcN` suffix: `1.0.0-rc3` → `1.0.0`
3. RC releases should be tested before promoting to stable

## Files Modified in a Release

| File | Change |
|------|--------|
| `CHANGELOG.md` | Add new version section |
| `custom_components/marstek/manifest.json` | Update `version` field |
| `pyproject.toml` | Update version if tracked |

## Example: Creating rc2 from rc1

```bash
# 1. Check what changed
git log --oneline v1.0.0-rc1..HEAD

# 2. Run validation
python3 -m mypy --strict custom_components/marstek/
pytest tests/ -q

# 3. Update manifest.json version to "1.0.0-rc2"
# 4. Update CHANGELOG.md with new section
# 5. Commit and tag
git add CHANGELOG.md custom_components/marstek/manifest.json
git commit -m "Release v1.0.0-rc2"
git tag -a v1.0.0-rc2 -m "Release v1.0.0-rc2"
git push origin main --tags
```
```
