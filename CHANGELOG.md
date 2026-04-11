# Changelog

## 1.0.0

### Patch Changes

- 07e7d8a: Fix grid input and output energy sensors when device counters stop advancing.
- 713f12d: Fix automated GitHub release creation after Changesets publishes a tag.
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
