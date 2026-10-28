---
next: false
---

# Development

Install and day-to-day use live in the [documentation index](README.md). This page is for working on the integration.

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

## Documentation site

`docs/` is also the source of the public site at [marstek.rhino-inquisitor.com](https://marstek.rhino-inquisitor.com/), built with [VitePress](https://vitepress.dev/) and deployed to GitHub Pages by `.github/workflows/docs.yaml` on every push to `main` that touches `docs/`. Pull requests that touch `docs/` run the same build, so a dead link fails the check instead of the deploy.

```bash
npm ci
npm run docs:dev      # live preview at http://localhost:5173
npm run docs:build    # the CI build, including the dead-link check
```

- Every page renders twice: on GitHub and on the site. Use plain relative links (`entities.md`, `../tools/...`). Links that leave `docs/`, and links to `README.md` or `marstek_device_openapi.MD`, are rewritten to GitHub at build time (`docs/.vitepress/config.mts`).
- Beginner pages that only make sense on the site live in `docs/guide/` and carry their `description` in front matter. Other pages get theirs from `docs/.vitepress/pages.ts`.
- New pages need a sidebar entry in `docs/.vitepress/config.mts`.
- Use GitHub alert syntax (`> [!TIP]`) for callouts. It renders on both GitHub and the site; VitePress `:::` containers do not render on GitHub.
- `guide/comparison.md` carries a review date. Recheck the other projects before you change it.

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

Next to the mocks, `fw-*` services run the vendor's own firmware in Renode for every image in `tools/firmware/catalog.json` ([tools/firmware_emulator/README.md](../tools/firmware_emulator/README.md)). Each costs 0.3-0.6 core and about 550 MB, so on a small machine start them by name, at most `nproc` at a time, and remove each batch before the next. The batch recipe is in AGENTS.md, *Sandbox resource budget*.

## Test matrix

This table is which device and firmware combinations we test, and how. The tests run in Docker (`.devcontainer/docker-compose.yml`). Each combination is one of three kinds:

- **Real firmware**: Marstek's own firmware image runs unmodified in a [Renode](https://renode.io/) emulator ([`tools/firmware_emulator/`](../tools/firmware_emulator/README.md)). Home Assistant adds and polls it like a physical battery. Every value it reports is computed by Marstek's code, so the units, scales, errors and dropped requests match a device on that firmware. The parts around the processor are simulated: an energy-counted battery, an inverter, PV strings on Venus A/D, and a CT003 meter served by [AstraMeter](https://github.com/tomquist/AstraMeter). Watts and state of charge follow a simulated home, and the firmware turns them into Open API values.
- **Mock only**: no public image of this firmware exists, so a Python mock ([`tools/mock_device/`](../tools/mock_device/README.md)) stands in. It returns what we believe that firmware sends, based on the shared firmware profile, and it runs a simulated home, battery and meter. It is only as accurate as our reading of the firmware, so reports from owners matter most here.
- **Not covered**: we have neither. [Tell us about it](#your-device-or-firmware-is-missing).

| Device | Reports as | Firmware (`ver`) | Coverage | Docker service (address) |
|--------|-----------|------------------|----------|--------------------------|
| Venus E 3.0 | `VenusE 3.0` | 144 | Real firmware | `fw-venus-e-144` (`172.28.0.53`) |
| | | 145 | Mock only | `mock-marstek` (`172.28.0.20`) ★ |
| | | 147 | Real firmware | `fw-venus-e-147` (`172.28.0.54`) |
| | | 1476 (app 147.6) | Real firmware | `fw-venus-e-1476` (`172.28.0.55`) |
| | | 148 | Real firmware | `fw-venus-e-148` (`172.28.0.56`) |
| | | 149 | Real firmware | `fw-venus-e-149` (`172.28.0.57`) |
| | | 150 | Real firmware | `fw-venus-e-150` (`172.28.0.50`) ★ |
| | | 151 | Real firmware | `fw-venus-e-151` (`172.28.0.70`) |
| Venus A | `Venus A` | 148 | Real firmware | `fw-venus-a-148` (`172.28.0.58:30001`) |
| | | 1487 (app 148.7) | Real firmware | `fw-venus-a-1487` (`172.28.0.59`) |
| | | 149 | Real firmware | `fw-venus-a-149` (`172.28.0.60:30003`) |
| | | 1491 (app 149.1) | Real firmware | `fw-venus-a-1491` (`172.28.0.73`) |
| | | 150 | Real firmware | `fw-venus-a-150` (`172.28.0.51:30004`) ★ |
| | | 1509 (app 150.9) | Real firmware | `fw-venus-a-1509` (`172.28.0.62`) |
| | | 151 | Real firmware | `fw-venus-a-151` (`172.28.0.74`) |
| Venus D | `Venus D` | 145 | Mock only | `mock-marstek-4` (`172.28.0.23:30002`) ★ |
| | | 147 | Real firmware | `fw-venus-d-147` (`172.28.0.63`) |
| | | 149 | Real firmware | `fw-venus-d-149` (`172.28.0.64`) |
| | | 1492 (app 149.2) | Real firmware | `fw-venus-d-1492` (`172.28.0.65`) |
| | | 150 | Real firmware | `fw-venus-d-150` (`172.28.0.52`) ★ |
| | | 151 | Real firmware | `fw-venus-d-151` (`172.28.0.71`) |
| Venus C | `VenusC` | 153 | Real firmware | `fw-venus-c-153` (`172.28.0.67`) |
| | | 155 | Real firmware | `fw-venus-c-155` (`172.28.0.68`) |
| | | 156 | Real firmware | `fw-venus-c-156` (`172.28.0.66`) ★ |
| Venus E 2.0 (not compatible) | `VenusE` | 153, 155 | Real firmware: the same HMG-50 image, run as Venus C | `fw-venus-c-153`, `fw-venus-c-155` |
| | | 156 | Real firmware | `fw-venus-e2-156` (`172.28.0.69`) |
| Venus E mini | `Venus E mini` | 145 | Mock only | `mock-marstek-8` (`172.28.0.28`) ★ |
| | `VNSEM-0` | 301 | Real firmware (PMU), answering the Open API in Renode. Reads and UDP rules follow [#86](https://github.com/taurgis/has-marstek-local-api/issues/86); replies go to `<a.b.c>.255`. Any `time_num` is acknowledged. Unknown methods stay silent. Passive `ES.SetMode` is a parse error with JSON-RPC id 0 and data 401, which matches [#104](https://github.com/taurgis/has-marstek-local-api/issues/104). A six-field Manual payload without `manual_set` leaves displayed power unset; `manual_set` 0-2 with unsigned `power` is documentation-based, not a capture | `fw-venus-e-mini-301` (`172.28.0.75`) |
| Unknown family | `VenusE Pro` | 1508 | Real firmware (a test build Marstek published under Venus A) | `fw-venus-a-1508` (`172.28.0.61`) |

Firmware images come from Marstek's public update server and community archives; [`tools/firmware/catalog.json`](../tools/firmware/catalog.json) lists each one with its source and SHA-256. Every real-firmware version also has a matching Python mock under `--profile mocks-all`, for quick sweeps that don't need the emulator's CPU.

### How we test in Docker

- **★ starts by default.** A plain `docker compose up -d` in `.devcontainer/` starts Home Assistant, the four mock-only devices and four emulators: Venus E 150, Venus A 150, Venus D 150 and Venus C 156. The mix of ports 30000, 30002 and 30004 also tests that devices on different Open API ports each get their own socket.
- **Other versions run in batches.** Each emulator needs about half a CPU core and 550 MB of RAM, so start the others by name, a few at a time, then remove them before the next batch:

  ```bash
  cd .devcontainer
  # Naming the -ct meter sidecar also starts its emulator
  docker compose up -d homeassistant fw-venus-e-148-ct fw-venus-d-149-ct
  # ... add them in Home Assistant, check the values, then:
  docker compose rm -sf fw-venus-e-148-ct fw-venus-e-148 fw-venus-d-149-ct fw-venus-d-149
  ```

  `--profile firmware-all` defines all 25 emulators, but running them all at once needs a large machine. On a 4-CPU machine, run at most four at a time.
- **Venus C and Venus E 2.0 get their own batch.** They drop many requests by design ([#82](https://github.com/taurgis/has-marstek-local-api/issues/82)), and that loss should be read as the firmware's shared meter channel, separate from machine load.
- **Automated tests never need Docker.** `pytest` uses the same firmware profiles against in-process mocks.

### Your device or firmware is missing?

If your combination is absent from the table above, or shows **Mock only**, please **[open a firmware coverage issue](https://github.com/taurgis/has-marstek-local-api/issues/new?template=firmware_coverage.yml)**. The issue form asks for the following.

1. **Your device model and firmware version.** In Home Assistant, go to **Settings** > **Devices & services** > **Marstek** and open the device. The **Device info** card shows the model and **Firmware**. That number is the Open API `ver` this table uses. If the integration is not set up yet, run `python3 tools/query_device.py <device IP> --port <Open API port>` from a checkout of this repository and copy `device` and `ver` from the reply. The Marstek app shows a version too, and it can be written differently: `147.6` in the app is `ver` `1476` here.
2. **What works and what does not.** Say so even if everything looks right. For a mock-only version, one owner confirming the values is what turns our guess into a checked fact.
3. **Diagnostics (optional).** On the device page, open the ⋮ menu and choose **Download diagnostics**. MAC addresses, IP addresses and the Wi-Fi name are redacted, but check the file before you attach it.

You can skip sending the firmware file itself. We look for a public copy on Marstek's update server and in the community archives. If you know where one is, add the link to the issue. Keep Marstek account details and passwords out of the issue.

## Repository map

```
has-marstek-local-api/
├── custom_components/marstek/
│   ├── __init__.py            # Integration setup and teardown
│   ├── config_flow.py         # Config flow, discovery, reauth, reconfigure
│   ├── options_flow.py        # Polling, network, power, dashboard, energy meter
│   ├── const.py               # Constants and per-family limits
│   ├── coordinator.py         # Tiered data update coordinator
│   ├── firmware_profile.py    # Family + ver → capability/scaling profile
│   ├── device_info.py         # Device registry metadata
│   ├── mode_config.py         # ES.SetMode payload construction
│   ├── power.py               # Power-limit validation
│   ├── device_action.py       # Charge / discharge / stop device actions
│   ├── discovery.py           # UDP discovery helpers
│   ├── scanner.py             # Background IP/firmware change detection
│   ├── diagnostics.py         # Downloadable diagnostics
│   ├── repairs.py             # Repair issues and fix flows
│   ├── binary_sensor.py       # CT/permission binary sensors
│   ├── number.py              # Depth of discharge
│   ├── select.py              # Operating mode select entity
│   ├── sensor.py              # All sensor entities
│   ├── switch.py              # Bluetooth advertising, panel LED
│   ├── services.py            # Service implementations
│   ├── services.yaml          # Service definitions
│   ├── helpers/               # Entity descriptions, UDP client pool, capacity store
│   └── pymarstek/             # UDP client library
├── tests/                     # Test suite
├── tools/                     # Mock device, firmware emulator, query tools
├── scripts/                   # Release and code-limit tooling
└── docs/                      # This documentation
```

## Protocol reference

See [Marstek Device Open API Rev 3.1](marstek_device_openapi.MD).
