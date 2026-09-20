# Contributing

Thanks for contributing! Please keep changes focused and follow the existing style.

## Development setup

```bash
# Create a virtual environment (Python 3.14.2+ — required by current HA Core tests)
python3.14 -m venv venv
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

The `Code Quality` workflow adds four blocking static checks. They need only
`pip install -r requirements_quality.txt`, so you can run them without the Home
Assistant test harness:

```bash
# Complexity, dead code and commented-out code across the whole repo
python3 -m ruff check custom_components tests tools scripts

# 1000 lines per file, 200 lines per function
python3 scripts/check_code_limits.py

# Unused code
python3 -m vulture

# Copy-paste detection
jscpd
```

See [Code quality gates](docs/development.md#code-quality-gates) for what each
threshold means and how to handle a file that outgrows the limit.

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
- Keep modules under 1000 lines; split along a seam instead of raising the limit.
