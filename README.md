# Marstek Home Assistant Integration

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.10%2B-blue.svg)](https://www.home-assistant.io/)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

A **custom Home Assistant integration** for monitoring and controlling Marstek energy storage devices (Venus A/D/E 3.0, Venus E mini, etc.) using **local UDP communication** via the Marstek **Open API Rev 3.1**. Capabilities, energy units, and schedule limits depend on **device family + firmware `ver`** — not every device exposes the same modes or encodings.

> **Note**: This is an independent community project and is **not affiliated with or endorsed by Marstek**. This integration was originally inspired by Marstek's reference implementation but has been completely rewritten with a custom architecture, UDP client, and feature set.

## Features

### Monitoring
- **Battery State of Charge (SoC)** - Current battery level percentage
- **Battery Power** - Real-time charge/discharge power (W)
- **Battery Temperature** - Battery pack temperature (disabled by default, see below)
- **Battery Status** - Current operational status
- **Operating Mode** - Current device mode (Auto, AI, Manual, Passive, and UPS on capable firmware)
- **PV Metrics** - Solar panel power, voltage, current, and state (up to 4 channels; Venus A/D only)
- **On-grid Power** - Total grid power from energy meter (3-phase support)
- **WiFi Signal Strength** - RSSI for connectivity diagnostics
- **CT Connection Status** - Current transformer connection state
- **Meter lifetime energy** - EM input/output totals when the firmware reports them
- **Device Information** - IP address, firmware version, MAC addresses

### Control
- **Operating Mode Selection** - Auto and AI (and UPS when the firmware profile allows it). Manual and Passive still require parameterized services.
- **Passive Mode Control** - Set charge/discharge power with duration (service)
- **Manual Scheduling** - Time-based charge/discharge slots: **0–9** on Venus A/C/D/E, **0–5** on Venus E mini
- **Bulk Schedule Management** - Set multiple schedules via YAML or clear all schedules
- **SYS settings** - Depth of discharge, Bluetooth advertising, and panel LED on capable firmware (write-only; restored last value)
- **Data Sync** - Trigger immediate device refresh on demand

### Architecture
- **Local UDP Communication** - No cloud dependency, fast and reliable
- **Automatic IP Discovery** - Finds devices via UDP broadcast
- **Dynamic IP Handling** - Background scanner detects and updates IP changes
- **Centralized Polling** - Single coordinator per device to avoid request bursts
- **Stable Entity IDs** - BLE-MAC-based identifiers survive IP changes

## Comparison with other community integrations

| Integration | Repository | Summary | Strengths | Tradeoffs |
|---|---|---|---|---|
| **This integration** | https://github.com/taurgis/has-marstek-local-api | Local UDP with centralized polling, scanner-based IP updates, strong HA patterns. | Shared UDP client, tiered polling, robust config flow/options/services, strict validation & typing. | Focused on per-device setup (no multi-device aggregation). |
| **Marstek Local API** | https://github.com/jaapp/ha-marstek-local-api | Feature-rich local API integration with multi-device aggregation. | Multi-device support, aggregate sensors, extensive diagnostics. | Heavier complexity; discovery may pause active clients. |
| **MarstekEnergy reference** | https://github.com/marstekEnergy/ha_marstek | Vendor reference implementation. | Simple setup, uses upstream py-marstek. | Less robust networking (per-entry sockets), fewer HA best-practice patterns. |

## Requirements

| Requirement | Version/Details |
|-------------|-----------------|
| Home Assistant | Core 2025.10.0+ |
| Network | Same LAN segment as Marstek devices |
| Device Config | **OPEN API must be enabled** in the Marstek app |
| UDP Port | 30000 (default, must be reachable) |

> **Warning**: This integration is currently **not compatible with Venus E2.0** devices. Using this integration with Venus E2.0 may cause disconnection between the device and CT003.

## Installation

### Method 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu and select **Custom repositories**
3. Add repository URL and select **Integration** as category
4. Search for "Marstek" and install
5. Restart Home Assistant

### Method 2: Manual Installation

1. Download or clone this repository
2. Copy the `custom_components/marstek` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

```bash
# Example for Home Assistant OS/Supervised
cd /config
mkdir -p custom_components
cp -r /path/to/ha_marstek/custom_components/marstek custom_components/
```

## Configuration

1. Go to **Settings** then **Devices and Services**
2. Click **Add Integration**
3. Search for **Marstek**
4. The integration will automatically discover devices on your network
5. Select your device and complete the setup

### Options

After setup, you can adjust polling and request behavior in **Device → Configure**:

- **Fast polling interval** (default: 30s): ES.GetMode, ES.GetStatus, EM.GetStatus (real-time power)
- **Medium polling interval** (default: 60s): PV.GetStatus (solar data, Venus A/D only)
- **Slow polling interval** (default: 300s): Wifi.GetStatus, Bat.GetStatus (diagnostics)

> **Note**: `Wifi.GetStatus` and `Bat.GetStatus` are only sent while at least one entity that depends on them is enabled. The battery detail entities (temperature, remaining/rated capacity, charge/discharge permission) are disabled by default on new installations because `Bat.GetStatus` is suspected to trigger device resets on some Marstek firmwares ([#14](https://github.com/taurgis/has-marstek-local-api/issues/14)). Enable them in the entity registry to opt in — the API call resumes automatically. **Upgrading from an earlier version does not disable already-registered entities**: if you want to stop `Bat.GetStatus`, disable these entities manually once (see [docs/entities.md](docs/entities.md)).
- **Parallel API requests** (default: off): Send enabled status APIs concurrently with no inter-request delay (advanced; can be less stable on Wi-Fi)
- **Request delay** (default: 5s): Delay between consecutive UDP requests in sequential mode (ignored when parallel API requests is enabled)
- **Request timeout** (default: 10s): Per-request timeout before retry/fail
- **Failures before unavailable** (default: 3): Consecutive failures before entities become unavailable

These values can be tuned to reduce network traffic or improve responsiveness.

### Data updates

The integration uses a single `DataUpdateCoordinator` per device with tiered polling to avoid request bursts. Entities never perform their own I/O and always read from coordinator data.

## Documentation

Extended documentation (with screenshots) lives in `docs/`:

- [Documentation index](docs/README.md)
- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Options](docs/options.md)
- [Entities](docs/entities.md)
- [Energy Dashboard](docs/energy_dashboard.md)
- [Services](docs/services.md)
- [Repairs](docs/repairs.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Open API Rev 3.1 reference](docs/marstek_device_openapi.MD)

## Supported Devices

Firmware `ver` comes from discovery (`Marstek.GetDevice`). Unknown or unparseable `ver` stays legacy-safe (no SYS/UPS; no invented energy scaling). Unsupported capability-gated entities are **omitted**, not left permanently unavailable.

| Device | Status | Notes |
|--------|--------|-------|
| Venus A 3.0 | Supported (PV) | Solar energy uses 0.01 kWh → Wh from firmware **149**; firmware **148 or older** stays Wh. PV channel 1 is deciwatts below 150 (including 148 and 149) and watts at 150+. SYS/UPS from 150 |
| Venus D 3.0 | Supported (PV) | SYS/UPS and watt PV encoding from firmware 150 |
| Venus C / Venus E 3.0 | Supported (no PV) | SYS/UPS from firmware 150; ten manual slots (0–9) |
| Venus E mini | Supported (no PV) | SYS without the 150 gate when `ver` is a known integer; UPS only at `ver >= 150`; **six** manual slots (0–5) |
| Venus E 2.0 | **Not compatible** | May disconnect the device from CT003 |
| Other OPEN API devices | May work (untested) | Treated as unknown family (legacy-safe) |

## Services

The integration provides several services for advanced control:

### marstek.set_passive_mode
Set the device to passive mode with specified power and duration.
- **power**: Target power in watts (-5000 to 5000). Negative charges, positive discharges.
- **duration**: Duration in seconds (default: 3600)

### marstek.set_manual_schedule
Configure a single manual schedule slot.
- **schedule_slot**: Slot number (`0–9` on Venus A/C/D/E, `0–5` on Venus E mini)
- **start_time**: Schedule start time
- **end_time**: Schedule end time
- **power**: Target power in watts
- **days**: Days of the week (mon, tue, wed, thu, fri, sat, sun)
- **enable**: Enable/disable the schedule

### marstek.set_manual_schedules
Configure multiple schedules via YAML:
```yaml
service: marstek.set_manual_schedules
data:
  device_id: "YOUR_DEVICE_ID"
  schedules:
    - schedule_slot: 0
      start_time: "08:00"
      end_time: "16:00"
      days: ["mon", "tue", "wed", "thu", "fri"]
      power: -2000
      enable: true
    - schedule_slot: 1
      start_time: "18:00"
      end_time: "22:00"
      power: 800
```

### marstek.clear_manual_schedules
Clear all manual schedule slots on the device.

### marstek.request_data_sync
Trigger an immediate data refresh from the device.

## Project Structure

```
ha_marstek/
├── custom_components/marstek/
│   ├── __init__.py           # Integration setup and teardown
│   ├── config_flow.py        # Config flow, discovery, reauth
│   ├── const.py              # Constants and configuration
│   ├── coordinator.py        # Data update coordinator
│   ├── device_action.py      # Device automation actions
│   ├── discovery.py          # UDP discovery helpers
│   ├── scanner.py            # Background IP change detection
│   ├── select.py             # Operating mode select entity
│   ├── sensor.py             # All sensor entities
│   ├── services.py           # Service implementations
│   ├── services.yaml         # Service definitions
│   ├── strings.json          # UI strings
│   ├── translations/         # Localization files
│   └── pymarstek/            # UDP client library
│       ├── udp.py            # UDP client implementation
│       ├── command_builder.py# Protocol command builder
│       ├── data_parser.py    # Response parser
│       └── const.py          # Protocol constants
├── tests/                    # Test suite
├── tools/                    # Development utilities
│   ├── mock_device/          # Mock device for testing
│   ├── capture_device.py     # Traffic capture tool
│   └── query_device.py       # Device query tool
└── docs/                     # Documentation
    └── marstek_device_openapi.MD  # Protocol reference
```

## Development

### Setting Up Development Environment

```bash
# Clone the repository
git clone https://github.com/your-username/ha_marstek.git
cd ha_marstek

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements_test.txt
```

### Running Tests

```bash
# Linting
python3 -m ruff check custom_components/marstek/

# Type checking
python3 -m mypy --strict custom_components/marstek/

# Tests with coverage
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95

# Run specific test file
pytest tests/test_config_flow.py -v
```

### Release workflow

This repository uses Changesets for release preparation.

```bash
# Install the release tooling once
npm install

# Add a release note for a change
npm run changeset
```

Merge changesets into `main` and the `Changesets` GitHub Action will open or update a `Release` pull request that:

- bumps `package.json`
- updates `CHANGELOG.md`
- syncs `custom_components/marstek/manifest.json`
- syncs `pyproject.toml`

When that PR is merged, the workflow creates and pushes the matching `v*` tag, and the existing release workflow publishes the GitHub release from `CHANGELOG.md`.

If you need to continue the current release-candidate flow, enter prerelease mode before merging the next release batch:

```bash
# Start generating RC versions
npm run changeset:pre:enter

# Leave prerelease mode before the final stable release
npm run changeset:pre:exit
```

Changesets prerelease mode uses SemVer prerelease identifiers such as `-rc.0`.

### Mock Device

A mock device is available for testing without physical hardware. Pass `--device` and `--ver` to select a firmware profile (legacy vs Rev 3.1 encodings and capabilities):

```bash
cd tools
python -m mock_device --ver 145
python -m mock_device --device VenusA --ver 148
python -m mock_device --device VenusA --ver 149
```

## Troubleshooting

### LED Light Switch
- Capable firmware (Venus A/C/D/E at firmware 150 or newer, and Venus E mini with a known firmware version) exposes a **Panel LED** switch.
- Older or unknown regular firmware does not get the switch, because those devices reject `Led.Ctrl`.
- The Open API has no readable LED state. Home Assistant restores the last value it successfully wrote; changes made in the Marstek app or on the device itself may not be reflected.
- Bluetooth advertising and depth of discharge use the same firmware gate and the same restored optimistic state.

### Missing UPS or SYS settings
- UPS appears on the operating-mode select only for ES-capable firmware `ver >= 150`.
- Depth of discharge, Bluetooth advertising, and panel LED use the SYS gate (Venus A/C/D/E at 150+, Venus E mini with a known integer `ver`).
- Download diagnostics and check `firmware_profile` if a control is missing. Unsupported features are omitted, not left unavailable.
- See [docs/troubleshooting.md](docs/troubleshooting.md).

### Device Not Found
- Ensure OPEN API is enabled in the Marstek app on your device
- Verify Home Assistant and device are on the same network segment
- Check that UDP port 30000 is not blocked by firewall
- Try restarting the device

### Connection Issues
- The integration uses UDP which may be affected by network instability
- Check WiFi signal strength (RSSI sensor) for connectivity quality
- Ensure only one client is communicating with the device at a time

### Entities Unavailable
- Check device connectivity and OPEN API status
- Review Home Assistant logs for error messages
- Expected behavior: brief UDP/API failures keep last known values until the
  "Failures before unavailable" threshold is reached (to avoid flapping on an
  unstable device API)
- Try the request_data_sync service

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Guidelines
- Follow existing code style and patterns
- Add tests for new functionality
- Update documentation as needed
- Keep commits focused and well-described

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Thanks to the Home Assistant community for their excellent documentation and patterns
- Protocol documentation: [Marstek Open API Specification](docs/marstek_device_openapi.MD)

## Disclaimer

This integration is provided "as is" without warranty of any kind. Use at your own risk. The authors are not responsible for any damage to your devices or data loss. This project is not affiliated with, endorsed by, or connected to Marstek in any way.
