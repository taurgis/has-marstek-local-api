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

Run the mock device to develop without hardware. `--device` and `--ver` select a firmware profile (legacy encodings + `Method not found` vs Rev 3.1 encodings and accepted SYS/UPS writes). Physical watts and watt-hours are encoded on the wire according to that profile; the integration normalizes them back to W and Wh.

```
cd tools
python -m mock_device --ver 145
python -m mock_device --device "VenusE 3.0" --ver 150
python -m mock_device --device "VenusA" --ver 150
```

Backwards-compatible shim (still works):

```
python tools/mock_device/mock_marstek.py
```

Devcontainer compose runs five mocks with mixed firmware. See [tools/mock_device/README.md](../tools/mock_device/README.md).

## Protocol reference

See [Marstek Device Open API Rev 3.1](marstek_device_openapi.MD).
