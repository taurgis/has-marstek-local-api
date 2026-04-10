# Contributing

Thanks for contributing! Please keep changes focused and follow the existing style.

## Development setup

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements_test.txt
```

## Verification

Run these before opening a PR:

```bash
# Linting
python3 -m ruff check custom_components/marstek/

# Type checking
python3 -m mypy --strict custom_components/marstek/

# Tests with coverage
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95
```

## Releases

Changesets now handles release preparation for this repository.

```bash
# Install release tooling
npm install

# Add a changeset on feature/fix branches when the change should be released
npm run changeset
```

After changesets land on `main`, GitHub Actions opens or updates a `Release` PR with the version bump, changelog changes, and synced Home Assistant metadata files. Merging that PR pushes the matching `v*` tag, and the existing release workflow turns that tag into a GitHub release.

For release candidates, enter prerelease mode before preparing the next RC batch and exit it before the final stable cut:

```bash
npm run changeset:pre:enter
npm run changeset:pre:exit
```

## Tips

- Add or update tests for changes in behavior.
- Keep user-facing strings in sync with translations.
- Prefer small, well-scoped commits for easier review.
