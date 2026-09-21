# Development

## Repo layout

- `custom_components/marstek/` — integration
- `custom_components/marstek/pymarstek/` — UDP client
- `tools/mock_device/` — mock device for local testing
- `tools/firmware/` — Control firmware hashes, OTA notes, and issue #15 analysis (blobs are not vendored)
- `docs/marstek_device_openapi.MD` — protocol reference

## Running tests

Pytest uses `pytest-homeassistant-custom-component`, which tracks current
Home Assistant Core and requires **Python 3.14.2+** (Core 2026.9). The
integration itself still supports Home Assistant **2025.10+**.

From repo root:

```
# Linting
python3 -m ruff check custom_components/marstek/

# Formatting (ruff is pinned, so this matches CI exactly)
python3 -m ruff format --check custom_components tests tools scripts

# Type checking
python3 -m mypy --strict custom_components/marstek/

# Tests with coverage
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95
```

## Code quality gates

The `Code Quality` workflow runs five static checks on every pull request and
blocks the merge when any of them fails. All five are configured in
`pyproject.toml` (plus `.jscpd.json`) so a local run and CI see the same rules.
`requirements_quality.txt` pins every tool, ruff included, because `ruff format`
output changes between releases — a floating version would make the formatting
gate disagree with a local run.

```bash
# Install the quality tooling once (separate from requirements_test.txt, so
# these checks run on any Python — no Home Assistant test harness needed)
pip install -r requirements_quality.txt

# 1. Complexity, dead code and commented-out code
python3 -m ruff check custom_components tests tools scripts

# 2. Formatting (drop --check to rewrite the files in place)
python3 -m ruff format --check custom_components tests tools scripts

# 3. File and function length ceilings
python3 scripts/check_code_limits.py

# 4. Unused code the linter cannot see
python3 -m vulture

# 5. Copy-paste detection
jscpd
```

What each one enforces:

| Check | Catches | Threshold |
| --- | --- | --- |
| ruff `C901` | Over-complex functions | McCabe 25, matching Home Assistant Core |
| ruff `PLR0911`–`PLR0915` | Too many returns / branches / arguments / statements | 10 / 25 / 12 / 90 |
| ruff `ERA` | Commented-out code | any |
| ruff `format --check` | Formatting drift | the formatter's own style at `line-length = 100` |
| `scripts/check_code_limits.py` | Oversized modules and functions | 1000 lines per file, 200 per function |
| vulture | Unreachable functions, unused attributes and variables | 80% confidence |
| jscpd | Duplicated blocks | 0.5% of the codebase, 15 lines / 70 tokens per clone |

`scripts/check_code_limits.py` exists because ruff has no module-length rule
and its closest function rule (`PLR0915`) counts statements rather than lines.
It is stdlib-only, reads its thresholds from `[tool.code-limits]`, and takes
`--max-file-lines` / `--max-function-lines` overrides for a one-off run.

When a module crosses 1000 lines, split it along a seam rather than raising the
ceiling: `pymarstek/udp.py` moved its broadcast sweep into
`pymarstek/udp_discovery.py` as a mixin, and the large test modules became
packages of themed modules with shared setup in `_helpers.py` and `conftest.py`.

Vulture and ruff both have escape hatches for code whose shape is not ours to
choose. Home Assistant fixes the signatures of `async_setup_entry`,
`async_turn_on` and friends, so unused-argument rules stay off and the
parameters HA passes into those hooks are listed in `[tool.vulture]
ignore_names`. Prefer a documented entry there over a blanket `# noqa`.

## Devcontainer Home Assistant image

`.devcontainer/docker-compose.yml` pins `ghcr.io/home-assistant/home-assistant:2026.9.3`. After changing the tag:

```
cd .devcontainer
docker compose pull homeassistant
docker compose up -d homeassistant
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

`--device` also picks the pack size and rated power, so a Venus A reports 2080 Wh / 1500 W rather than a Venus E's 5120 Wh / 2500 W. The mock simulates a whole home: a two-peak residential load curve (~10 kWh/day), a rooftop PV array following real solar geometry (`--house-pv-wp`, default 3500 Wp), and an Auto mode that regulates the simulated P1/CT reading toward zero through a lagged measurement, a deadband and a ramp-limited inverter. `ES.GetStatus.ongrid_power` is the inverter's own AC port; `EM.GetStatus.total_power` is the meter. They are different numbers, as they are on real hardware.

```
cd tools
python -m mock_device --ver 145
python -m mock_device --device "VenusE 3.0" --ver 150
python -m mock_device --device "VenusA" --ver 148
python -m mock_device --device "VenusA" --ver 149
python -m mock_device --device "VenusA" --ver 150
python -m mock_device --device "VenusC" --ver 153
python -m mock_device --device "VenusC" --ver 156
python -m mock_device --device "VenusA" --ver 1487
python -m mock_device --device "Venus E mini" --ver 145
```

Backwards-compatible shim (still works):

```
python tools/mock_device/mock_marstek.py
```

Devcontainer compose runs the archived Control mock matrix with mixed firmware and mixed Open API ports. See [tools/mock_device/README.md](../tools/mock_device/README.md).

## Protocol reference

See [Marstek Device Open API Rev 3.1](marstek_device_openapi.MD).
