# Development

## Repo layout

- `custom_components/marstek/` — integration
- `custom_components/marstek/pymarstek/` — UDP client
- `tools/mock_device/` — mock device for local testing
- `docs/marstek_device_openapi.MD` — protocol reference

## Running tests

From repo root:

```
# Linting
python3 -m ruff check custom_components/marstek/

# Type checking
python3 -m mypy --strict custom_components/marstek/

# Tests with coverage
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95
```

## Releases

Release preparation uses Changesets:

```bash
# Install release tooling once
npm install

# Add a changeset describing the user-facing change
npm run changeset
```

The `Changesets` GitHub Action keeps a `Release` PR up to date on `main`. Its version step updates `CHANGELOG.md`, `package.json`, `custom_components/marstek/manifest.json`, and `pyproject.toml`. After that PR is merged, the workflow pushes the matching `v*` tag and the existing release workflow creates the GitHub release.

If you are continuing an RC train, enter prerelease mode first:

```bash
npm run changeset:pre:enter
```

When the next release should be stable again:

```bash
npm run changeset:pre:exit
```

## Mock device

Run the mock device to develop without hardware:

```
cd tools
python -m mock_device
```

Backwards-compatible shim (still works):

```
python tools/mock_device/mock_marstek.py
```

## Protocol reference

See `docs/marstek_device_openapi.MD`.
