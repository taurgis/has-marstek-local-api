# Changesets

This repository uses Changesets to prepare releases.

- Run `npm install` once to install the release tooling.
- Run `npm run changeset` when a change should appear in the next release.
- Run `npm run changeset:version` to apply pending changesets and sync the release version into `custom_components/marstek/manifest.json` and `pyproject.toml`.
- Run `npm run changeset:pre:enter` before an RC train if you want Changesets to keep generating prereleases.
- Run `npm run changeset:pre:exit` when the next release should drop the prerelease suffix.

GitHub Actions opens and updates the release PR automatically from changesets merged into `main`.