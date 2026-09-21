# Changelog

## 1.2.0

### Minor Changes

- 26225a7: Add a delete option for a device a config entry no longer represents, so a battery whose identity MAC changed can be cleared from the device registry.

### Patch Changes

- 50f55c5: Cap and rotate container logs for every devcontainer service, which Docker leaves unbounded by default.
- 26225a7: Register each device's Wi-Fi and Bluetooth MAC with the device registry, so Home Assistant links the battery to the same hardware seen by DHCP and router integrations instead of showing it twice.
- 3c8a162: Discover Marstek devices that listen on Open API port 30004 without a manual IP entry.
- 9815152: Ignore stray datagrams that are not JSON objects during a discovery sweep, so unrelated traffic on the Open API port no longer aborts the search.
- b2f8b08: Read the network interface table in a worker thread and ask Home Assistant which adapters are enabled, so a discovery sweep no longer blocks the event loop and reaches every adapter Home Assistant knows about.
- 1619a53: Listen on every discovery port at once instead of polling them in turn, so a reply is never delayed or dropped by a quiet port, and resolve a hostname once per query rather than blocking the event loop on every received datagram.
- ab6cb6d: Leave replies without a device identity out of the discovery picker, so unrelated traffic on the Open API port no longer offers a phantom device that cannot be added.
- 26225a7: Document the integration's known limitations, its use cases, and how to remove it.
- 99aa716: Remove the unused household consumption placeholder from device status, which no parser filled and no entity read.
- a840389: Remove grid meter power sensors on firmware that never answers EM.GetStatus, such as HMG-50 reporting as Venus C below 155, where they could only ever read unknown.
- 8aa389e: Ignore impossible lifetime energy spikes so a bad grid, solar, load, or meter reading cannot freeze Home Assistant at a huge total.
- 26225a7: Move the entity wiring every platform repeats into a single `entity.py` base class, so unique ids and device info are built in one place.
- 35e00e7: Warn about reset-prone firmware before the first poll, skip Bat.GetStatus on those builds, and serialize their UDP requests.
- fc2caa5: Add Venus A 150 and Venus E mini Docker mocks that match GitHub-issue Open API wire shapes.
- 9815152: Read a firmware version sent as a JSON number the same way as its dotted text label, so a Rev 3.1 device keeps its SYS and UPS entities.
- 104b768: Warn on Control firmware below 150 that can disable Local API, skip JSON-RPC id 0, and ignore parallel requests on those builds.
- 88874e0: Keep older firmware safer during IP changes and discovery pauses, and skip polling Venus E2.0 entries that were added before that guard existed.
- 2b3f74f: Harden older-firmware Open API traffic: wait out in-flight polls before writes, skip Venus E2.0, and keep reset protections if unload fails.
- 1ea7966: Keep the integration compatible with Home Assistant 2026.9: use current device-registry identifier APIs, and map Open API mode names like Auto onto enum sensor and select options. Home Assistant 2025.10 remains supported.
- 41143f1: Align the config flow, runtime data and setup errors with Home Assistant core review conventions: the manual step now collects errors and shows one form at the end like the other steps, config_flow.py lists its steps in walk-through order with the helpers below them, runtime data is frozen, and setup and polling failures carry translated messages.
- fae8dd8: Match the Venus E 2.0 mock to HMG-50 Control 153: GetDevice identity VenusE, and no Open API EM.GetStatus until firmware 155.
- 758a44b: Keep Open API traffic minimal on HMG-50 Control (Venus C 2.0), where the Local API shares one Wi-Fi receive channel with the device's own UDP meter client (a Marstek CT or a Shelly): parallel requests and Wi-Fi retransmits now stay off on every HMG-50 build including 156, and a repair warning explains why Self-consumption charging can stall.
- 1ea048f: Reject Venus E 2.0 devices that report as HMG-50 or bare VenusE instead of treating them as Venus E 3.0.
- c8048c6: Drop the unused `marstek` logger name from the manifest, so enabling debug logging no longer targets a namespace the integration never writes to.
- 472f503: Match mock Open API replies to archived Control firmware: HMG-50 Wifi.SetConfig, 153 bat_power, and Set.Ver only where the recv list includes it.
- 1c4e23f: Add per-family pack sizes and rated power, conversion losses, inverter ramp limiting and a thermal model to the mock device, plus `--house-pv-wp` and `--phases` to configure the simulated home.
- 50f55c5: Stop the mock device answering JSON-RPC replies, so a shared UDP port can no longer spin it into a packet storm that fills the disk with log output.
- 1c4e23f: Make the mock devices emit physically realistic values: report `ongrid_power` as the inverter's own AC port instead of the meter reading, regulate Auto mode as a closed loop on the simulated P1/CT, and give the simulated home a two-peak load curve and a rooftop PV array.
- 7f80923: Ship pymarstek.network in the mock Docker image so LAN reply-port behavior matches firmware.
- f192dc4: Restore the Venus E 3.0 firmware 150 LAN capture entry in the Open API reference and list the current modules in the contributor code map.
- 8d1bd2f: Fix a poll cycle aborting when a device answers with an unexpected payload shape by making every response parser return defaults instead of raising.
- b2f8b08: Declare PARALLEL_UPDATES on the sensor and binary sensor platforms, which Home Assistant's quality scale requires every platform to set explicitly.
- b2f8b08: Keep a failed status read from ending a parallel poll early and leaving its sibling reads sending to a device whose poll is already over.
- b8aed1c: Share the solar channel sum with the poll validity check, so a channel that reports a non-numeric value no longer fails the whole update.
- b8aed1c: Read the per-channel solar breakdown when a device also reports an array total, instead of dropping channels 2-4 and reporting channel 1 at a tenth of its real power.
- 9815152: Keep polling when a solar channel reports a value that is not a number, instead of losing the whole update for that device.
- 2d7f74a: Record the missing docs-triggers and docs-conditions quality scale rules as exempt and document both coverage floors in the README and contributor guide.
- 26225a7: Log routine setup, discovery and service activity at debug level, so the default log keeps only the connection-loss, recovery and device-change events worth reading.
- b2f8b08: Log one warning when a device stops answering and one line when it comes back, instead of a warning for every failed request of every failed poll, and stop republishing the same repair issue each cycle.
- 198cd80: Refresh the README and docs against the current code: rebuild the stale project-structure tree, document the power options and per-family power caps, list the scanned Open API discovery ports, correct the socket-limit device families, and re-verify the community-integration comparison table as of 2026-09-20.
- 7a419bf: Fix a glitched device reply permanently poisoning sensor values by rejecting non-finite numbers at the UDP boundary and in the status merge.
- a840389: Set a device up exactly once when its address changes, instead of twice from discovery and repairs or not at all when a reconfigure corrected an unreachable device, and stop Home Assistant logging a deprecation warning about this integration on every reauth or reconfigure.
- 660a53b: Add a `ruff format` gate to the quality workflow and apply the formatter across the project, with ruff pinned so local runs and CI agree.
- b2f8b08: Stop a running discovery sweep from holding up a Home Assistant shutdown, and debounce repeat discovery of unconfigured devices on a UTC clock so a daylight saving change cannot skip or repeat an hour of it.
- c8048c6: Report a numeric sensor as unknown when firmware answers with a placeholder string or a boolean instead of a number, so a reading is no longer dropped with a traceback on every poll, while a number the device quoted is still read as a number.
- 2d7f74a: Raise validation errors instead of generic errors when a mode or depth-of-discharge value is rejected, so Home Assistant shows the reason without a stack trace.
- 3467776: Release the Open API UDP socket when setup fails after the client is leased, without dropping sockets held for SETUP_RETRY.
- 61d1c89: Build every device dict, device identifier and discovery port list from one shared helper so discovery, the scanner and the config flow cannot drift apart.
- 7b1d047: Send every device write through one retry loop and one polling-pause helper, and share the SYS entity, schema and command-builder code that the number, switch, select, service and device-action paths had each copied, so a fix to the write path now reaches all of them.
- 43650aa: Treat only real MAC addresses as device identity and abort duplicate setup when BLE and Wi-Fi identities overlap.
- e1b3053: Keep the integration's shared state behind one typed `HassKey` so the UDP client pool, its locks and the lease bookkeeping are reached by attribute instead of by string key.
- 2d7f74a: Annotate the options flow with the integration's typed config entry and drop two unreachable guards from the config flow, so the setup path matches Home Assistant's runtime-data rule under strict typing.
- 67e1ed9: Split the Open API UDP client and the config flow into focused modules — reply routing, request pacing, poll gating, firmware marks, command statistics, poll composition and the options flow — so each behaviour can be changed and tested on its own.
- 21a562f: Move broadcast discovery out of the Open API UDP client into its own module so the sweep, the reply mapping and the discovery cache can be changed without touching the socket and unicast paths.
- 8c20ffa: Keep the Open API UDP listener running after an unexpected decode error so polling does not stall.
- 4619d4b: Read the whole UDP datagram a device sends, so a long schedule reply is no longer truncated into broken JSON and reported as an unreachable device.
- eee2dc9: Match Open API replies to the sending device, keep stale sensors off when there is no cache, and reject Venus E 2.0 during connection repairs.
- 016c7df: Fix UDP wire handling and firmware gating: send datagrams through the event loop instead of blocking on a non-blocking socket, throttle hosts that merely end in `.255`, exempt real subnet broadcasts of any prefix length, keep the reply deadline from being spent on the per-device throttle, stop broadcast discovery dropping replies from its final interval, reload a config entry when a firmware update changes a measurement scale, and treat every HMG-50 Control build from generation 153 as HMG-50.
- a982274: Match Venus C 153 and later to HMG-50 Control: no SYS/UPS, poll EM.GetStatus only from firmware 155, and treat Open API as reset-prone until 156.
- 15ff778: Retransmit read-only Open API unicasts on known-safe firmware (Control 150+ / HMG-50 156+) after a silent 500 ms wait, staying inside the configured request timeout, so Wi-Fi timeouts recover without extra copies on writes, LAN, or unknown firmware.
- 3f069fa: Fix Wi-Fi identity matching, serialize discovery traffic, and release UDP sockets when a retrying device is removed.
- 26225a7: Give the Wi-Fi IP address, gateway, subnet mask and DNS sensors icons instead of leaving them blank.

## 1.1.3

### Patch Changes

- a8b1188: Document Chrome DevTools restore (Chrome 136+ non-default profile) and CDP-first Home Assistant UI testing. Extend `ha_cdp.py` for config-entry delete/re-add, discovery flow wait, live entity updates, device actions (`execute_script`), automations, reconfigure/options/diagnostics, and script upsert (HA rejects `id` in the script body).
- 387cbac: Teach the HA Chrome CDP helper to disable and re-enable config entries, devices, and entities, list repair issues, and drive the cannot-connect Fix flow used when a device drops off the network.
- c894f06: Fix PV1 showing 10× too high on firmware 150.9 by keeping channel-1 deciwatt scaling, independent of the solar-energy fix.
- f402adb: Fix setup and polling by sending UDP from each device's configured Open API port so firmware that replies there can answer, including mixed custom ports. Reuse the existing UDP client for same-port GetDevice during manual add and Confirm device instead of binding a second socket.
- c10d660: Teach the HA Chrome CDP helper to cover Home Assistant surfaces the integration already participates in but had not been live-tested: Ignore discovery, system options, repair ignore, setup_retry, device rename/area/labels, hide entity, Assist expose, history, debug logging, and energy validation.
- 5bccdae: Count ignored config entries when deciding which BLE MACs are already configured so Ignore then Unignore can rediscover the device on the next scan.

## 1.1.2

### Patch Changes

- 12265ed: Add Venus A firmware 148 and 149 Docker mocks so 1.1.0 keeps 148 on the 1.0.0 encodings and 149 on the scaled solar unit.
- ecda43c: Treat dotted firmware labels such as 148.3 as the Open API integer prefix so Venus A 148 keeps legacy energy and PV scales.

## 1.1.1

### Patch Changes

- e0ec8e8: Fix set_passive_mode failing with Device not found when Home Assistant device IDs are truncated or passed as a config entry ID, MAC, or entity ID.

## 1.1.0

### Minor Changes

- ddb13d4: Add Open API Rev 3.1 firmware-profile support with UPS mode, SYS DOD/Bluetooth/LED controls, field-specific energy scaling, EM lifetime energy sensors, and automatic reload when firmware capabilities change.

### Patch Changes

- e2399f1: Make the Bat.GetStatus polling call opt-in: the battery detail entities (temperature, remaining/rated capacity, charge/discharge permission) are now disabled by default, and the integration only sends Bat.GetStatus while at least one of them is enabled. The call is suspected to trigger spontaneous device resets on some Marstek firmwares (#14). Existing installations keep their currently enabled entities; disable the battery detail entities manually to stop the call.
- 668878e: Add a shared firmware profile contract with versioned mock and device-specific schedule limits.
- 56e61e9: Fix Venus A firmware 149+ solar energy totals that were stored about 10× too low.
- 3bf94af: Accept integer ES.GetMode values and instance id 1 as read fallbacks, and map VNSA/VNSD/VNSE3 discovery names, without changing string SetMode writes or the id=0 default.
- 00fc5b9: Keep last-known CT readings when Venus E 3.0 firmware 150 GetMode reports zeros, and add a matching firmware 150 Docker mock beside the existing firmware 145 Venus E.

## 1.0.0

### Patch Changes

- 5e8e22c: Document that LED light switch support is unavailable until Marstek exposes a stable local API for it.
- 07e7d8a: Fix grid input and output energy sensors when device counters stop advancing.
- c452cd6: Fix discovery to normalize leading-zero IPv4 addresses before storing device hosts.
- 713f12d: Fix automated GitHub release creation after Changesets publishes a tag.
- d61c79e: Fix Venus A passive mode validation to allow commands up to 1500 W.
- 321f528: Avoid misleading Venus A energy totals when firmware reports contradictory zero values.

All notable changes to this project will be documented in this file.

## [1.0.0-rc8] - 2026-03-19

### Added

- Manual IP/port entry in the config flow
- Multi-port discovery support for devices using custom Open API ports

### Changed

- Battery icon handling now follows Home Assistant's battery device class behavior

### Fixed

- Preserved custom ports during discovery and device info lookups
- Wrapped UDP request IDs to 16-bit values and accepted wrapped `id = 0` responses

### Maintenance

- Refined discovery internals and development mocks
- Updated typing compatibility for the latest Home Assistant/Python environment

## [1.0.0-rc7] - 2026-03-03

### Added

- Parallel API request option for faster polling
- Marstek PV sensor data preparation
- Automated issue labeling workflow
- Slow response troubleshooting guidance
- Agent workflow documentation

### Fixed

- Corrected idle battery power behavior when the device omits status

### Maintenance

- Ensured service setup on initialization
- Expanded tests for config entry reload, service persistence, and reauth/discovery reload

## [1.0.0-rc6] - 2026-02-09

### Added

- API command stability sensors
- Device metadata updates from discovery

### Changed

- Increased max duration option ranges

### Fixed

- Standardized sensor existence checks

### Maintenance

- Refined discovery/service error handling
- Refactored UDP operations to asyncio loop
- Kept services registered for integration lifetime
- Organized code into helper modules
- Expanded tests and snapshots; improved reliability and coverage
- Clarified failure threshold documentation

## [1.0.0-rc5] - 2026-02-07

### Added

- Autodiscovery support for automatic device detection

### Fixed

- Device action now uses real power values instead of absolute values
- Battery power API failure no longer sets battery to idle incorrectly
- Grid power field values are now preserved correctly on API failures

### Maintenance

- Updated device naming
- Updated CONTRIBUTING with verification info
- Code cleanup and refactoring
- Test adjustments

## [1.0.0-rc4] - 2026-02-06

### Added

- Device action configuration validation
- Official Docs Researcher agent and expanded researcher tooling

### Changed

- Refined entity naming and battery sensor default visibility; updated quality scale

### Fixed

- Preserve `bat_capacity` and `bat_rated_capacity` values
- Ensure entities are created even if the API fails on first fetch
- Keep previous values correctly on API failures
- Translation message adjustment

### Maintenance

- Energy dashboard documentation and troubleshooting/bug report updates
- Research guidelines and Marstek research source documentation
- Tests for failed API fallback and test setup cleanup

## [1.0.0-rc3] - 2026-02-01

### Added

- Mock device state persistence
- Command diagnostics for UDP client
- GitHub issue templates for bugs and feature requests

### Changed

- Enhanced PV and battery power reporting (including Venus A PV support)
- Refined grid/on-grid sensor naming for clarity
- Expanded error messaging with new translation keys
- Simplified diagnostics command stats

### Fixed

- Aligned battery power behavior to Home Assistant Energy dashboard expectations
- Corrected PV power scaling and inaccurate pv_power reporting

### Maintenance

- Improved test helpers and coverage enforcement
- Documentation updates (Venus A/D PV support, comparisons, dev/testing guidance)
- Cleanup: unused imports and logger definition; repository layout tweaks

## [1.0.0-rc2] - 2026-01-29

### Added

- Device actions now support configurable power settings
- Release automation via GitHub Actions

### Changed

- Enhanced IP change detection with event-driven scans (faster response to network changes)
- Dynamically adjusted device action timings for better reliability
- Lowered API request delay for improved responsiveness
- Adjusted scanner discovery frequency

### Maintenance

- Enforced strict typing across the codebase
- Updated Home Assistant entity callbacks to latest patterns
- Added comprehensive test suite for integration
- Updated quality scale compliance documentation
- Added release management skill for standardized releases

## [1.0.0-rc1] - 2026-01-28

Initial release.
