"""Scanner for Marstek devices — detects IP and firmware changes."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, ClassVar, Self

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DEFAULT_UDP_PORT, DOMAIN
from .discovery import discover_devices
from .firmware_profile import is_unsupported_venus_e2
from .helpers.broadcast import async_broadcast_addresses
from .helpers.entry_metadata import (
    apply_entry_metadata_update,
    async_refresh_entry_from_unicast,
)
from .helpers.flow_helpers import (
    formatted_mac_or_none,
    identities_overlap,
    identity_macs_from_entry,
    identity_macs_from_mapping,
)
from .helpers.ports import discovery_scan_ports
from .helpers.udp_clients import (
    async_paused_udp_receivers,
    get_udp_client_for_entry,
    paused_discovery_sockets,
)

_LOGGER = logging.getLogger(__name__)

# Scanner runs discovery every 10 minutes as a backup
# Primary detection is event-driven: triggered when coordinator hits failure threshold
SCAN_INTERVAL = timedelta(minutes=10)

# Minimum time between event-triggered scans (debounce)
MIN_SCAN_INTERVAL = timedelta(seconds=30)

# Minimum time between discovery flows for unconfigured devices
UNCONFIGURED_DISCOVERY_DEBOUNCE = timedelta(hours=1)

# Setup already queries GetDevice once. Skip a second unicast if the
# scanner's initial sweep finishes in the same minute.
UNICAST_REFRESH_COOLDOWN = timedelta(seconds=60)


def _build_discovery_flow_data(device: dict[str, Any]) -> dict[str, Any]:
    """Build discovery flow data from device info."""
    flow_data: dict[str, Any] = {
        "ip": device.get("ip"),
        "ble_mac": device.get("ble_mac"),
        "device_type": device.get("device_type"),
        "version": device.get("version"),
        "wifi_name": device.get("wifi_name"),
        "wifi_mac": device.get("wifi_mac"),
        "mac": device.get("mac"),
    }

    discovered_port = device.get("port")
    normalized_port: int | None = None
    if isinstance(discovered_port, int):
        normalized_port = discovered_port
    elif isinstance(discovered_port, str):
        try:
            normalized_port = int(discovered_port)
        except ValueError:
            normalized_port = None

    if normalized_port is not None and 1 <= normalized_port <= 65535:
        flow_data["port"] = normalized_port

    return flow_data


class MarstekScanner:
    """Scanner for Marstek devices that detects IP address changes."""

    _scanner: ClassVar[Self | None] = None

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the scanner."""
        self._hass = hass
        self._track_interval: CALLBACK_TYPE | None = None
        self._scan_task: asyncio.Task[None] | None = None
        self._last_scan_monotonic: float | None = None
        self._last_scan_at: datetime | None = None
        self._last_broadcast_device_count: int | None = None
        self._last_broadcast_error: str | None = None
        self._last_unicast_refreshes: list[dict[str, Any]] = []
        self._last_unicast_monotonic: dict[str, float] = {}
        self._unconfigured_seen: dict[str, datetime] = {}

    @classmethod
    @callback
    def async_get(cls, hass: HomeAssistant) -> Self:
        """Get singleton scanner instance."""
        if cls._scanner is None:
            cls._scanner = cls(hass)
        return cls._scanner

    @classmethod
    @callback
    def async_reset(cls) -> None:
        """Reset the singleton scanner instance.

        Should be called when the last config entry is unloaded to ensure
        clean state on reload and avoid stale references during testing.
        """
        cls._scanner = None

    async def async_setup(self) -> None:
        """Initialize scanner and start periodic scanning."""
        if self._track_interval is not None:
            _LOGGER.debug("Marstek scanner already initialized")
            return
        _LOGGER.debug("Initializing Marstek scanner")
        # No need to create persistent UDP client - create new instance for each scan
        # This avoids state issues and conflicts with concurrent requests

        # Start periodic scanning
        self._track_interval = async_track_time_interval(
            self._hass,
            self.async_scan,
            SCAN_INTERVAL,
            cancel_on_shutdown=True,
        )

        # Execute initial scan immediately
        self.async_scan()

    async def async_unload(self) -> None:
        """Stop periodic scanning and cleanup resources."""
        if self._track_interval is not None:
            self._track_interval()
            self._track_interval = None
            _LOGGER.debug("Marstek scanner stopped")

        # Cancel any running scan task
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None

    @callback
    def async_scan(self, now: datetime | None = None) -> None:
        """Periodically scan for devices and check IP changes."""
        # Cancel previous scan if still running (shouldn't happen normally)
        if self._scan_task is not None and not self._scan_task.done():
            _LOGGER.debug("Previous scan still running, skipping")
            return

        # A sweep waits out DISCOVERY_TIMEOUT for replies. As a background
        # task it neither holds up startup nor makes async_block_till_done
        # (and so Home Assistant's shutdown) wait for that timeout; Home
        # Assistant cancels it at shutdown instead.
        self._scan_task = self._hass.async_create_background_task(
            self._async_scan_impl(), name="Marstek device scan"
        )
        self._last_scan_monotonic = time.monotonic()

    @callback
    def async_request_scan(self) -> bool:
        """Request an immediate scan (event-driven, e.g., on connection failure).

        This allows the coordinator to trigger a scan when it detects connection
        failures, enabling faster IP change detection without aggressive polling.

        Returns:
            True if scan was triggered, False if debounced (too soon after last scan)
        """
        # Debounce: don't scan if we recently scanned
        if self._last_scan_monotonic is not None:
            elapsed = time.monotonic() - self._last_scan_monotonic
            min_interval = MIN_SCAN_INTERVAL.total_seconds()
            if elapsed < min_interval:
                _LOGGER.debug(
                    "Scan request debounced (last scan %.2fs ago, min interval %.2fs)",
                    elapsed,
                    min_interval,
                )
                return False

        _LOGGER.debug("Immediate scan requested (connection failure detected)")
        self.async_scan()
        return True

    def note_firmware_query(self, entry_id: str) -> None:
        """Record that *entry_id* already ran a unicast GetDevice recently."""
        self._last_unicast_monotonic[entry_id] = time.monotonic()

    def diagnostics(self) -> dict[str, Any]:
        """Return the last scan snapshot for config-entry diagnostics."""
        last_scan_at = self._last_scan_at
        return {
            "last_scan_at": last_scan_at.isoformat() if last_scan_at is not None else None,
            "broadcast_device_count": self._last_broadcast_device_count,
            "broadcast_error": self._last_broadcast_error,
            "unicast_refreshes": list(self._last_unicast_refreshes),
        }

    async def _async_scan_impl(self) -> None:
        """Execute device discovery and check for IP and firmware changes."""
        devices: list[dict[str, Any]] = []
        broadcast_error: str | None = None
        try:
            # Use local discovery module (workaround for pymarstek echo issues)
            _LOGGER.debug("Scanner: Starting device discovery (broadcast)")
            scan_ports = self._build_scan_ports()
            broadcast_addresses = await async_broadcast_addresses(self._hass)
            async with async_paused_udp_receivers(self._hass) as paused:
                discovered = await discover_devices(
                    ports=scan_ports,
                    broadcast_addresses=broadcast_addresses,
                    shared_sockets=paused_discovery_sockets(paused),
                )
            devices = discovered if isinstance(discovered, list) else []
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Scanner discovery failed")
            broadcast_error = "discovery_failed"

        _LOGGER.debug("Scanner: Discovered %d device(s)", len(devices))
        for device in devices:
            _LOGGER.debug(
                "  Device: %s at IP %s (BLE-MAC: %s, WiFi-MAC: %s)",
                device.get("device_type", "Unknown"),
                device.get("ip", "Unknown"),
                device.get("ble_mac", "N/A"),
                device.get("wifi_mac", "N/A"),
            )

        matched_entry_ids = self._match_discovered_entries(devices)
        unicast_refreshes = await self._async_unicast_unmatched_entries(matched_entry_ids)
        self._record_scan_result(devices, broadcast_error, unicast_refreshes)

        if not devices:
            return

        try:
            configured_macs = self._get_configured_macs()
            self._prune_unconfigured_cache(configured_macs)
            self._trigger_unconfigured_discovery(devices, configured_macs)
        except Exception:
            _LOGGER.exception("Scanner failed while advertising unconfigured devices")

    def _match_discovered_entries(self, devices: list[dict[str, Any]]) -> set[str]:
        """Apply broadcast hits to config entries and return matched entry ids."""
        matched: set[str] = set()
        if not devices:
            return matched
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            try:
                if self._process_discovered_entry(entry, devices):
                    matched.add(entry.entry_id)
            except Exception:
                _LOGGER.exception(
                    "Scanner failed while processing entry %s",
                    entry.entry_id,
                )
        return matched

    async def _async_unicast_unmatched_entries(
        self, matched_entry_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Query GetDevice on loaded entries the broadcast sweep did not see.

        Broadcast does not cross VLANs. Unicast to the stored IP still
        reaches those devices and is how a firmware update is learned
        without deleting the entry.
        """
        results: list[dict[str, Any]] = []
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id in matched_entry_ids:
                continue
            if entry.state != ConfigEntryState.LOADED:
                continue
            if not self._unicast_cooldown_elapsed(entry.entry_id):
                continue
            udp_client = get_udp_client_for_entry(self._hass, entry)
            if udp_client is None:
                continue
            self.note_firmware_query(entry.entry_id)
            try:
                result = await async_refresh_entry_from_unicast(
                    self._hass,
                    entry,
                    udp_client,
                )
            except Exception:
                # Applying the update can raise; one entry must not end the
                # scan before the others and the unconfigured-device pass run.
                _LOGGER.exception(
                    "Scanner failed while refreshing firmware for %s",
                    entry.entry_id,
                )
                result = {"entry_id": entry.entry_id, "status": "error"}
            results.append(result)
        return results

    def _unicast_cooldown_elapsed(self, entry_id: str) -> bool:
        """Return True when another unicast GetDevice is allowed for *entry_id*."""
        last = self._last_unicast_monotonic.get(entry_id)
        if last is None:
            return True
        return (time.monotonic() - last) >= UNICAST_REFRESH_COOLDOWN.total_seconds()

    def _record_scan_result(
        self,
        devices: list[dict[str, Any]],
        broadcast_error: str | None,
        unicast_refreshes: list[dict[str, Any]],
    ) -> None:
        """Store the last scan snapshot for diagnostics."""
        self._last_scan_at = dt_util.utcnow()
        self._last_broadcast_device_count = len(devices)
        self._last_broadcast_error = broadcast_error
        self._last_unicast_refreshes = unicast_refreshes

    def _process_discovered_entry(
        self,
        entry: config_entries.ConfigEntry,
        devices: list[dict[str, Any]],
    ) -> bool:
        """Match one config entry against discovered devices and update it.

        Returns True when a broadcast reply belonged to this entry, so the
        unicast firmware fallback can skip it.
        """
        _LOGGER.debug(
            "Scanner: Checking entry %s (state: %s)",
            entry.title,
            entry.state,
        )
        if entry.state not in (
            ConfigEntryState.LOADED,
            ConfigEntryState.SETUP_RETRY,
        ):
            _LOGGER.debug(
                "Scanner: Skipping entry %s - state is %s (not LOADED)",
                entry.title,
                entry.state,
            )
            return False

        stored_macs = identity_macs_from_entry(entry)
        stored_ip = entry.data.get(CONF_HOST)
        stored_port = int(entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))

        _LOGGER.debug(
            "Scanner: Entry %s - stored MACs: %s, stored IP: %s",
            entry.title,
            stored_macs or "N/A",
            stored_ip or "N/A",
        )

        if not stored_macs or not stored_ip:
            _LOGGER.debug(
                "Scanner: Skipping entry %s - missing identity MAC or IP",
                entry.title,
            )
            return False

        matched_device = self._find_device_by_identity(devices, stored_macs, entry.title)

        if not matched_device:
            _LOGGER.debug(
                "Scanner: No matching device found for entry %s (MACs: %s)",
                entry.title,
                stored_macs,
            )
            return False

        new_ip = matched_device.get("ip")
        new_port = int(matched_device.get("port", DEFAULT_UDP_PORT))
        _LOGGER.debug(
            "Scanner: Entry %s - current %s:%s, discovered %s:%s",
            entry.title,
            stored_ip,
            stored_port,
            new_ip,
            new_port,
        )
        ip_changed = bool(new_ip and new_ip != stored_ip)
        port_changed = bool(new_ip and new_port != stored_port)
        if ip_changed or port_changed:
            _LOGGER.info(
                "Scanner detected endpoint change for device %s: %s:%s -> %s:%s",
                stored_macs,
                stored_ip,
                stored_port,
                new_ip,
                new_port,
            )
            discovery_flow.async_create_flow(
                self._hass,
                DOMAIN,
                context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                data=_build_discovery_flow_data(matched_device),
            )
            return True

        self._maybe_update_entry_metadata(entry, matched_device)
        _LOGGER.debug(
            "Scanner: Entry %s endpoint unchanged (%s:%s)",
            entry.title,
            stored_ip,
            stored_port,
        )
        return True

    def _build_scan_ports(self) -> list[int]:
        """Build the UDP port list for discovery scans."""
        return discovery_scan_ports(self._hass.config_entries.async_entries(DOMAIN))

    def _maybe_update_entry_metadata(
        self,
        entry: config_entries.ConfigEntry,
        device: dict[str, Any],
    ) -> None:
        """Update stored device metadata if discovery reports changes."""
        apply_entry_metadata_update(self._hass, entry, device)

    def _find_device_by_identity(
        self,
        devices: list[dict[str, Any]],
        stored_macs: set[str],
        entry_title: str,
    ) -> dict[str, Any] | None:
        """Find a discovered device that shares any stable MAC with an entry."""
        if not stored_macs:
            return None
        for device in devices:
            device_macs = identity_macs_from_mapping(device)
            if not identities_overlap(stored_macs, device_macs):
                continue
            _LOGGER.debug(
                "Scanner: Identity match found for entry %s (%s)",
                entry_title,
                stored_macs & device_macs,
            )
            return device
        return None

    def _get_configured_macs(self) -> set[str]:
        """Collect all configured MACs for this integration."""
        configured: set[str] = set()
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            configured.update(identity_macs_from_entry(entry))
        return configured

    def _prune_unconfigured_cache(self, configured_macs: set[str]) -> None:
        """Drop cached unconfigured devices that are now configured."""
        for mac in list(self._unconfigured_seen):
            if mac in configured_macs:
                self._unconfigured_seen.pop(mac, None)

    def _flow_identity_macs(self, flow: Mapping[str, Any]) -> set[str]:
        """Collect identity MACs from an in-progress config flow."""
        macs: set[str] = set()
        context = flow.get("context", {})
        unique_id = formatted_mac_or_none(context.get("unique_id"))
        if unique_id is not None:
            macs.add(unique_id)
        data = flow.get("data", {})
        if isinstance(data, dict):
            macs.update(identity_macs_from_mapping(data))
        return macs

    def _has_pending_discovery(self, macs: str | set[str]) -> bool:
        """Return True if a discovery flow is already in progress for this device."""
        if isinstance(macs, str):
            formatted = formatted_mac_or_none(macs)
            wanted = {formatted} if formatted is not None else set()
        else:
            wanted = macs
        if not wanted:
            return False

        flows = self._hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        for flow in flows:
            context = flow.get("context", {})
            if not isinstance(context, dict):
                continue
            if context.get("source") != config_entries.SOURCE_INTEGRATION_DISCOVERY:
                continue
            if identities_overlap(wanted, self._flow_identity_macs(flow)):
                return True
        return False

    def _should_trigger_unconfigured(self, identity_macs: set[str]) -> bool:
        """Return True if we should trigger a discovery flow for this device."""
        if not identity_macs:
            return False
        if self._has_pending_discovery(identity_macs):
            return False

        # UTC: a naive local clock repeats or skips an hour at a DST
        # change, which would fire or swallow a debounced discovery flow.
        now = dt_util.utcnow()
        for mac in identity_macs:
            last_seen = self._unconfigured_seen.get(mac)
            if last_seen and (now - last_seen) < UNCONFIGURED_DISCOVERY_DEBOUNCE:
                return False

        for mac in identity_macs:
            self._unconfigured_seen[mac] = now
        return True

    def _trigger_unconfigured_discovery(
        self, devices: list[dict[str, Any]], configured_macs: set[str]
    ) -> None:
        """Create discovery flows for devices not yet configured."""
        for device in devices:
            device_ip = device.get("ip")
            device_macs = identity_macs_from_mapping(device)
            if not device_ip or not device_macs:
                continue

            if identities_overlap(device_macs, configured_macs):
                continue

            if is_unsupported_venus_e2(device.get("device_type")):
                continue

            if not self._should_trigger_unconfigured(device_macs):
                continue

            _LOGGER.debug(
                "Scanner discovered unconfigured device %s at %s",
                device_macs,
                device_ip,
            )
            discovery_flow.async_create_flow(
                self._hass,
                DOMAIN,
                context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
                data=_build_discovery_flow_data(device),
            )
