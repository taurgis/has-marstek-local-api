"""Data update coordinator for Marstek devices."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .capacity_estimator import UsableCapacityEstimator, apply_capacity_estimate
from .const import (
    BAT_STATUS_KEYS,
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_PLUG_ENERGY_IN,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_PARALLEL_API_REQUESTS,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_UDP_PORT,
    DOMAIN,
    INITIAL_SETUP_REQUEST_DELAY,
    WIFI_STATUS_KEYS,
)
from .firmware_profile import FirmwareProfile, resolve_firmware_profile
from .helpers.boot_pulse import async_track_boot_pulses, plug_power_entity
from .helpers.capacity_store import CapacityEstimateStore
from .helpers.coordinator_helpers import raise_if_invalid_status
from .helpers.plug_round_trip import (
    PLUG_STORE_KEY,
    PlugRoundTrip,
    apply_plug_round_trip,
    plug_from_options,
)
from .helpers.restart_detector import (
    LOG_VERBS,
    RESTARTS_ATTR_KEY,
    RESTARTS_DATA_KEY,
    RESTARTS_STORE_KEY,
    RestartDetector,
    restart_attributes,
)
from .pymarstek import MarstekUDPClient
from .scanner import MarstekScanner

_LOGGER = logging.getLogger(__name__)


class MarstekDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Per-device data update coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        udp_client: MarstekUDPClient,
        device_ip: str,
        device_port: int = DEFAULT_UDP_PORT,
        *,
        is_initial_setup: bool = False,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            config_entry: Config entry for this device
            udp_client: UDP client for communication
            device_ip: IP address of the device
            is_initial_setup: If True, use faster delays for initial data fetch
        """
        self.udp_client = udp_client
        self.config_entry = config_entry
        # Use initial IP/port, but read from config_entry.data dynamically
        self._initial_device_ip = device_ip
        self._initial_device_port = device_port

        # Track last fetch times for tiered polling. None means "never fetched"
        # so the first cycle always runs even when time.monotonic() is still
        # below the interval (fresh CI runners / recently rebooted hosts).
        # WiFi and battery details are stamped separately so that enabling an
        # entity mid-cycle triggers its first fetch on the next update instead
        # of waiting out a shared slow-tier timestamp bumped by a skip.
        self._last_pv_fetch: float | None = None  # Medium interval
        self._last_wifi_fetch: float | None = None  # Slow interval (WiFi status)
        self._last_bat_fetch: float | None = None  # Slow interval (battery details)

        # Track if this is the initial setup (use faster delays)
        self._is_initial_setup = is_initial_setup

        # Diagnostics tracking - exposed for diagnostics.py
        self.last_update_success_time: datetime | None = None
        self.last_update_attempt_time: datetime | None = None
        self.consecutive_failures: int = 0
        # True once this run of failures has been reported. Home Assistant
        # asks integrations to log the transition into and out of being
        # unreachable, not every failed poll:
        # https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable
        self._failure_logged: bool = False
        self._marked_reset_prone_ip: str | None = None
        self._marked_retransmit_safe_ip: str | None = None
        # Usable capacity is derived from polls this coordinator already makes.
        # The store is loaded on the first successful fetch.
        self._capacity = UsableCapacityEstimator()
        self._capacity_store = CapacityEstimateStore(hass, config_entry.entry_id)
        self._capacity_loaded = False
        # Restarts and dropouts, told apart from what a boot leaves in the
        # polls. Stored with the capacity windows.
        self._restarts = RestartDetector()
        # Round trip and standby from a linked energy meter, when one is set.
        self._plug: PlugRoundTrip | None = None

        # Get configured fast polling interval
        fast_interval = config_entry.options.get(
            CONF_POLL_INTERVAL_FAST, DEFAULT_POLL_INTERVAL_FAST
        )
        self._capacity_poll_interval = float(fast_interval)
        self._capacity.set_poll_interval(self._capacity_poll_interval)

        super().__init__(
            hass,
            _LOGGER,
            name=f"Marstek {device_ip}",
            update_interval=timedelta(seconds=fast_interval),
            config_entry=config_entry,
            always_update=False,
        )

        # Store config entry reference that is guaranteed to be non-None
        # (The parent class allows None, but we always pass a config entry)
        self._entry: ConfigEntry = config_entry

        # Log configured intervals
        medium_interval = self._get_medium_interval()
        slow_interval = self._get_slow_interval()
        request_delay = self._get_request_delay()
        _LOGGER.debug(
            "Device %s:%s polling coordinator started, interval: %ss (fast), "
            "%ss (medium/PV), %ss (slow/WiFi+Bat), delay: %ss%s",
            device_ip,
            device_port,
            fast_interval,
            medium_interval,
            slow_interval,
            request_delay,
            " [INITIAL SETUP - fast delays]" if is_initial_setup else "",
        )

    @staticmethod
    def _interval_elapsed(last_fetch: float | None, current_time: float, interval: float) -> bool:
        """Return True if this tier has never been fetched or its interval elapsed."""
        return last_fetch is None or (current_time - last_fetch) >= interval

    def _select_polling_tiers(self, current_time: float) -> tuple[bool, bool, bool]:
        """Decide which polling tiers to include for this update cycle."""
        include_pv = self.profile.supports_pv and self._interval_elapsed(
            self._last_pv_fetch, current_time, self._get_medium_interval()
        )
        slow_interval = self._get_slow_interval()
        include_wifi = (
            self._interval_elapsed(self._last_wifi_fetch, current_time, slow_interval)
            and self._is_wifi_status_enabled()
        )
        include_bat = (
            self._interval_elapsed(self._last_bat_fetch, current_time, slow_interval)
            and self._is_bat_status_enabled()
        )
        return include_pv, include_wifi, include_bat

    def _handle_update_error(self, current_ip: str, err: Exception) -> dict[str, Any]:
        """Handle polling errors and return cached data or raise UpdateFailed."""
        cached = self.data
        self.consecutive_failures += 1
        self._restarts.record_failure()
        failure_threshold = self._get_failure_threshold()
        # One line per run of failures, not one per poll: a device offline
        # overnight at the default 30s interval would otherwise write
        # thousands of warnings and bury whatever else went wrong.
        first_report = not self._failure_logged
        self._failure_logged = True
        report = _LOGGER.warning if first_report else _LOGGER.debug

        if not cached or self.consecutive_failures >= failure_threshold:
            report(
                "Device %s status request failed (attempt #%d, threshold: %d): %s. "
                "Entities will become unavailable. "
                "Triggering immediate scan for IP changes",
                current_ip,
                self.consecutive_failures,
                failure_threshold,
                err,
            )
            self._create_connection_issue(str(err))
            # Trigger immediate scan to detect IP changes faster
            # (event-driven approach instead of aggressive polling)
            scanner = MarstekScanner.async_get(self.hass)
            scanner.async_request_scan()
            # Mark update as failed so entities become unavailable
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={
                    "host": current_ip,
                    "attempt": str(self.consecutive_failures),
                    "error": str(err),
                },
            ) from err

        # Below threshold with a cache - keep entities available
        report(
            "Device %s status request failed (attempt #%d of %d): %s. "
            "Keeping entities available with cached data",
            current_ip,
            self.consecutive_failures,
            failure_threshold,
            err,
        )
        return cached

    def _get_medium_interval(self) -> int:
        """Get medium polling interval from options."""
        return int(self._entry.options.get(CONF_POLL_INTERVAL_MEDIUM, DEFAULT_POLL_INTERVAL_MEDIUM))

    def _get_slow_interval(self) -> int:
        """Get slow polling interval from options."""
        return int(self._entry.options.get(CONF_POLL_INTERVAL_SLOW, DEFAULT_POLL_INTERVAL_SLOW))

    def _use_parallel_api_requests(self) -> bool:
        """Return True when polling should call status APIs in parallel.

        Firmware below Control v150 leaks/crashes the JSON heap under concurrent
        UDP (issues #14/#15), so parallel mode is ignored on those builds.
        """
        if not self.profile.parallel_requests_safe:
            return False
        return bool(
            self._entry.options.get(
                CONF_PARALLEL_API_REQUESTS,
                DEFAULT_PARALLEL_API_REQUESTS,
            )
        )

    def finish_initial_setup(self) -> None:
        """Mark initial setup as complete.

        Should be called after the first successful data fetch to switch
        from fast initial delays to normal configured delays.
        """
        self._is_initial_setup = False

    def _get_request_delay(self) -> float:
        """Get delay between requests from options, or fast delay for initial setup."""
        if self._use_parallel_api_requests():
            return 0.0
        configured = float(self._entry.options.get(CONF_REQUEST_DELAY, DEFAULT_REQUEST_DELAY))
        # Reset-prone Control builds stay at the configured spacing even during
        # the first fetch; the 2s initial shortcut is for firmware 150+.
        if self._is_initial_setup and not self.profile.openapi_reset_prone:
            return INITIAL_SETUP_REQUEST_DELAY
        return configured

    def _get_request_timeout(self) -> float:
        """Get timeout for API requests from options."""
        return float(self._entry.options.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))

    def _get_failure_threshold(self) -> int:
        """Get failure threshold from options (failures before entities become unavailable)."""
        return int(self._entry.options.get(CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD))

    @staticmethod
    def _entity_key_from_unique_id(unique_id: str) -> str | None:
        """Return the entity key from a MAC-based unique_id (`{mac}_{key}`)."""
        _, separator, key = unique_id.partition("_")
        return key if separator and key else None

    def _has_enabled_entities(self, keys: frozenset[str]) -> bool:
        """Return True if any entity with one of these keys is enabled."""
        entity_registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(entity_registry, self._entry.entry_id)
        for entry in entries:
            if entry.disabled_by is not None:
                continue
            key = self._entity_key_from_unique_id(entry.unique_id or "")
            if key is not None and key in keys:
                return True
        return False

    def _is_wifi_status_enabled(self) -> bool:
        """Return True if any WiFi status entity is enabled for this entry."""
        return self._has_enabled_entities(WIFI_STATUS_KEYS)

    def _is_bat_status_enabled(self) -> bool:
        """Return True if Bat.GetStatus may be sent for this entry.

        Bat.GetStatus is suspected to trigger device resets on some firmwares
        (issue #14). Reset-prone Control builds never send it. On later
        firmware the request is only sent while a user has enabled one of the
        entities that depend on it.
        """
        if self.profile.openapi_reset_prone:
            return False
        return self._has_enabled_entities(BAT_STATUS_KEYS)

    def _sync_openapi_udp_marks(self) -> None:
        """Align the UDP client's per-IP Open API marks with this device.

        Two marks, both keyed by IP: reset-prone (serialize every request) and
        retransmit-safe (a Wi-Fi copy is allowed). An IP change moves both.
        """
        current_ip = self.device_ip
        previous = self._marked_reset_prone_ip
        previous_safe = self._marked_retransmit_safe_ip
        prone = self.profile.openapi_reset_prone
        wifi_safe = self.profile.openapi_wifi_retransmit_safe
        if previous is not None and previous != current_ip:
            self.udp_client.clear_openapi_reset_prone(previous, owner=self._entry.entry_id)
        if previous_safe is not None and previous_safe != current_ip:
            self.udp_client.set_openapi_retransmit_safe(previous_safe, False)
        self.udp_client.set_openapi_reset_prone(current_ip, prone, owner=self._entry.entry_id)
        self.udp_client.set_openapi_retransmit_safe(current_ip, wifi_safe)
        self._marked_reset_prone_ip = current_ip if prone else None
        self._marked_retransmit_safe_ip = current_ip if wifi_safe else None

    @property
    def profile(self) -> FirmwareProfile:
        """Resolve the current firmware profile from config-entry metadata."""
        return resolve_firmware_profile(
            self._entry.data.get("device_type"),
            self._entry.data.get("version"),
        )

    @property
    def device_ip(self) -> str:
        """Get current device IP from config entry (supports dynamic IP updates)."""
        ip = self._entry.data.get(CONF_HOST)
        return str(ip) if ip else self._initial_device_ip

    @property
    def device_port(self) -> int:
        """Get current device port from config entry (supports dynamic updates)."""
        port = self._entry.data.get(CONF_PORT)
        return int(port) if port else self._initial_device_port

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data using library's get_device_status method with tiered polling.

        Tiered polling intervals (configurable per device):
        - Fast (base interval): ES.GetMode, ES.GetStatus, and EM.GetStatus
          when the firmware profile serves it (HMG-50 / Venus C 153 does not)
        - Medium: PV.GetStatus - solar data
        - Slow: Wifi.GetStatus, Bat.GetStatus - rarely changes

        Wifi.GetStatus and Bat.GetStatus are only sent while at least one
        entity that depends on them is enabled in the entity registry.
        """
        current_ip = self.device_ip
        current_port = self.device_port
        self.last_update_attempt_time = dt_util.now()
        _LOGGER.debug("Start polling device: %s:%s", current_ip, current_port)
        self._sync_openapi_udp_marks()

        if not await self.udp_client.begin_poll_cycle(current_ip):
            _LOGGER.debug("Polling paused for device: %s, skipping update", current_ip)
            return self.data or {}

        try:
            return await self._async_fetch_device_status(current_ip, current_port)
        finally:
            await self.udp_client.end_poll_cycle(current_ip)

    async def _async_fetch_device_status(
        self, current_ip: str, current_port: int
    ) -> dict[str, Any]:
        """Fetch one polling cycle after the poll-cycle lease is held."""
        # Determine which data types to fetch based on elapsed time
        current_time = time.monotonic()
        parallel_requests = self._use_parallel_api_requests()
        request_delay = self._get_request_delay()

        include_pv, include_wifi, include_bat = self._select_polling_tiers(current_time)

        # Get configured timeout
        request_timeout = self._get_request_timeout()

        _LOGGER.debug(
            "Polling tiers for %s: fast=always, pv=%s, wifi=%s, bat=%s, parallel=%s",
            current_ip,
            include_pv,
            include_wifi,
            include_bat,
            parallel_requests,
        )

        try:
            # Use library method to get device status
            # Pass flags to control which data types to fetch
            # Device requires delay between requests for stability (configurable)
            device_status = await self.udp_client.get_device_status(
                current_ip,
                port=current_port,
                timeout=request_timeout,
                include_pv=include_pv,
                include_wifi=include_wifi,
                include_em=self.profile.supports_em_status,
                include_bat=include_bat,
                parallel_requests=parallel_requests,
                delay_between_requests=request_delay,
                previous_status=self.data,  # Preserve values on partial failures
                profile=self.profile,
            )

            # Update last fetch times for successful fetches
            if include_pv:
                self._last_pv_fetch = current_time
            if include_wifi:
                self._last_wifi_fetch = current_time
            if include_bat:
                self._last_bat_fetch = current_time

            # Check if we actually got valid data
            raise_if_invalid_status(current_ip, device_status, _LOGGER)
            _LOGGER.debug(
                "Device %s poll done: SOC %s%%, Power %sW, Mode %s, Status %s "
                "(pv=%s, wifi=%s, bat=%s)",
                current_ip,
                device_status.get("battery_soc"),
                device_status.get("battery_power"),
                device_status.get("device_mode"),
                device_status.get("battery_status"),
                include_pv,
                include_wifi,
                include_bat,
            )

            # Update success tracking
            self.last_update_success_time = dt_util.now()
            if self._failure_logged:
                _LOGGER.info(
                    "Device %s is answering again after %d failed attempt(s)",
                    current_ip,
                    self.consecutive_failures,
                )
                self._failure_logged = False
            self.consecutive_failures = 0

            # Clear any existing connection issue on successful update
            self._clear_connection_issue()
            # es_status_fresh is false when ES.GetStatus did not answer and
            # battery_power was carried forward. The recorder still runs so
            # the sensors keep their last estimate. The capacity estimator
            # treats that poll as a hole and leaves the open window alone.
            await self._async_record_capacity(device_status, previous_status=self.data)

            return device_status
        except (TimeoutError, OSError, ValueError) as err:
            # Connection failed - Scanner will detect IP changes and update config entry
            return self._handle_update_error(current_ip, err)

    async def _async_record_capacity(
        self,
        device_status: dict[str, Any],
        *,
        previous_status: dict[str, Any] | None = None,
    ) -> None:
        """Fold this poll into the usable-capacity estimate and attach it.

        No extra UDP. The result stays unknown until enough clean windows exist.
        The restart log is closed here too: it is kept in the same store, so
        both load together and one delayed save writes both.
        """
        if not self._capacity_loaded:
            stored = await self._capacity_store.async_load()
            self._capacity = UsableCapacityEstimator.from_dict(stored)
            self._capacity.set_poll_interval(self._capacity_poll_interval)
            self._plug = plug_from_options(self._entry.options, stored)
            self._restarts.load((stored or {}).get(RESTARTS_STORE_KEY))
            self._capacity_loaded = True
        now = time.time()
        self._record_restarts(device_status, previous_status, now)
        apply_capacity_estimate(self._capacity, device_status, now)
        if self._plug is not None:
            apply_plug_round_trip(self._plug, self.hass, device_status, now)
        plug_dirty = self._plug is not None and self._plug.consume_dirty()
        restarts_dirty = self._restarts.consume_dirty()
        capacity_dirty = self._capacity.consume_dirty()
        if not (capacity_dirty or plug_dirty or restarts_dirty):
            return
        estimate = device_status.get("battery_usable_capacity")
        if capacity_dirty and estimate is not None:
            _LOGGER.info(
                "Device %s usable capacity is %s Wh after %d window(s)",
                self.device_ip,
                estimate,
                self._capacity.windows_accepted,
            )
        self._capacity_store.delay_save(self._store_payload())

    def _record_restarts(
        self,
        device_status: dict[str, Any],
        previous_status: dict[str, Any] | None,
        now: float,
    ) -> None:
        """Close any outage this poll ends and attach the restart log."""
        outage = self._restarts.record_success(now, previous_status, device_status)
        if outage is not None:
            _LOGGER.info(
                "Device %s %s: offline %.0fs over %d failed poll(s)%s",
                self.device_ip,
                LOG_VERBS[outage.kind],
                outage.offline_s,
                outage.failed_polls,
                f" ({', '.join(outage.evidence)})" if outage.evidence else "",
            )
        device_status[RESTARTS_DATA_KEY] = self._restarts.restarts
        device_status[RESTARTS_ATTR_KEY] = restart_attributes(self._restarts, now)

    def _store_payload(self) -> dict[str, Any]:
        """Return the capacity windows, the restart log and any meter readings."""
        payload = self._capacity.to_dict()
        payload[RESTARTS_STORE_KEY] = self._restarts.to_dict()
        if self._plug is not None:
            payload[PLUG_STORE_KEY] = self._plug.to_dict()
        return payload

    def async_track_plug_boot_tests(self) -> CALLBACK_TYPE | None:
        """Watch a linked plug's watts for the self-test the unit runs as it boots.

        Event-driven from the plug's own sensor: no request goes to the device.
        """
        power = plug_power_entity(self.hass, self._entry.options.get(CONF_PLUG_ENERGY_IN))
        if power is None:
            return None
        return async_track_boot_pulses(self.hass, power, self._on_boot_pulse)

    @callback
    def _on_boot_pulse(self, at: float) -> None:
        """Hold the plug's self-test for the next good poll."""
        _LOGGER.info("Device %s ran its boot self-test at the plug", self.device_ip)
        self._restarts.record_boot_pulse(at)

    async def async_save_capacity_estimate(self) -> None:
        """Flush accepted windows. A reload before the first poll must not wipe them."""
        if not self._capacity_loaded:
            return
        await self._capacity_store.async_save(self._store_payload())

    @callback
    def async_save_on_stop(self) -> CALLBACK_TYPE:
        """Write the capacity store as Home Assistant stops.

        A delayed save only follows a run or a correction, so the stored last
        poll can be hours old. Written at stop, it lets the first poll after
        a short restart continue the climb toward the next full charge.
        """

        async def _save(_event: Event) -> None:
            await self.async_save_capacity_estimate()

        return self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _save)

    def clear_openapi_reset_mark(self) -> None:
        """Drop this device's reset-prone UDP flag, including stale IPs."""
        previous = self._marked_reset_prone_ip
        previous_safe = self._marked_retransmit_safe_ip
        current = self.device_ip
        initial = self._initial_device_ip
        ips = {previous, previous_safe, current, initial}
        for ip in ips:
            if isinstance(ip, str) and ip:
                self.udp_client.clear_openapi_reset_prone(ip, owner=self._entry.entry_id)
                self.udp_client.set_openapi_retransmit_safe(ip, False)
        self.udp_client.clear_openapi_reset_prone_owner(self._entry.entry_id)
        self._marked_reset_prone_ip = None
        self._marked_retransmit_safe_ip = None

    def _issue_id(self) -> str:
        return f"cannot_connect_{self._entry.entry_id}"

    def _create_connection_issue(self, error: str) -> None:
        """Create a fixable connection issue for this entry.

        Raising the same issue again on every failed poll republishes it to
        the repairs panel each time, so only the first failure of a run
        creates it. Recovery deletes it, which re-arms this.
        """
        if ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id()):
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(),
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="cannot_connect",
            translation_placeholders={"host": self.device_ip, "error": error},
            data={"entry_id": self._entry.entry_id},
        )

    def _clear_connection_issue(self) -> None:
        """Clear the connection issue if it exists."""
        issue_registry = ir.async_get(self.hass)
        issue_id = self._issue_id()
        if issue_registry.async_get_issue(DOMAIN, issue_id):
            issue_registry.async_delete(DOMAIN, issue_id)
