# Changelog

All notable changes to this project will be documented in this file.

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
