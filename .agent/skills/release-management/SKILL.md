```skill
---
name: release-management
description: Guide for creating releases, updating changelogs, version bumps, and git tagging for the Marstek integration
---

# Release Management

This skill covers the complete release workflow for the Marstek Home Assistant integration.

## Release Types

| Type | Version Format | Example | Use Case |
|------|---------------|---------|----------|
| Release Candidate | `X.Y.Z-rcN` | `1.0.0-rc2` | Pre-release testing |
| Stable | `X.Y.Z` | `1.0.0` | Production release |
| Patch | `X.Y.Z` | `1.0.1` | Bug fixes |
| Minor | `X.Y.0` | `1.1.0` | New features (backward compatible) |
| Major | `X.0.0` | `2.0.0` | Breaking changes |

## Release Checklist

### 1. Pre-Release Validation

```bash
# Type checking (strict mode required)
python3 -m mypy --strict custom_components/marstek/

# Run all tests
pytest tests/ -q

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
