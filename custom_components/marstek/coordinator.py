"""Data update coordinator for Marstek devices."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from typing import Any

from .pymarstek import MarstekUDPClient

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FAILURE_THRESHOLD,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_POLL_INTERVAL_FAST,
    DEFAULT_POLL_INTERVAL_MEDIUM,
    DEFAULT_POLL_INTERVAL_SLOW,
    DEFAULT_REQUEST_DELAY,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_UDP_PORT,
    DOMAIN,
    INITIAL_SETUP_REQUEST_DELAY,
    device_supports_pv,
)

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
        
        # Check device capabilities based on device type
        # Per API docs (Chapter 4): Only Venus D supports PV, not Venus C/E
        device_type = config_entry.data.get("device_type", "")
        self._supports_pv = device_supports_pv(device_type)
        
        # Track last fetch times for tiered polling
        self._last_pv_fetch: float = 0.0  # Medium interval
        self._last_slow_fetch: float = 0.0  # Slow interval (WiFi, battery details)
        
        # Track if this is the initial setup (use faster delays)
        self._is_initial_setup = is_initial_setup
        
        # Diagnostics tracking - exposed for diagnostics.py
        self.last_update_success_time: datetime | None = None
        self.last_update_attempt_time: datetime | None = None
        self.consecutive_failures: int = 0
        
        # Get configured fast polling interval
        fast_interval = config_entry.options.get(
            CONF_POLL_INTERVAL_FAST, DEFAULT_POLL_INTERVAL_FAST
        )
        
        super().__init__(
            hass,
            _LOGGER,
            name=f"Marstek {device_ip}",
            update_interval=timedelta(seconds=fast_interval),
            config_entry=config_entry,
            always_update=False,
        )
        
        # Log configured intervals
        medium_interval = self._get_medium_interval()
        slow_interval = self._get_slow_interval()
        request_delay = self._get_request_delay()
        _LOGGER.debug(
            "Device %s:%s polling coordinator started, interval: %ss (fast), %ss (medium/PV), %ss (slow/WiFi+Bat), delay: %ss%s",
            device_ip,
            device_port,
            fast_interval,
            medium_interval,
            slow_interval,
            request_delay,
            " [INITIAL SETUP - fast delays]" if is_initial_setup else "",
        )

    
    def _get_medium_interval(self) -> int:
        """Get medium polling interval from options."""
        return self.config_entry.options.get(
            CONF_POLL_INTERVAL_MEDIUM, DEFAULT_POLL_INTERVAL_MEDIUM
        )
    
    def _get_slow_interval(self) -> int:
        """Get slow polling interval from options."""
        return self.config_entry.options.get(
            CONF_POLL_INTERVAL_SLOW, DEFAULT_POLL_INTERVAL_SLOW
        )
    
    def finish_initial_setup(self) -> None:
        """Mark initial setup as complete.
        
        Should be called after the first successful data fetch to switch 
        from fast initial delays to normal configured delays.
        """
        self._is_initial_setup = False
    
    def _get_request_delay(self) -> float:
        """Get delay between requests from options, or fast delay for initial setup."""
        if self._is_initial_setup:
            return INITIAL_SETUP_REQUEST_DELAY
        return self.config_entry.options.get(
            CONF_REQUEST_DELAY, DEFAULT_REQUEST_DELAY
        )

    def _get_request_timeout(self) -> float:
        """Get timeout for API requests from options."""
        return self.config_entry.options.get(
            CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT
        )

    def _get_failure_threshold(self) -> int:
        """Get failure threshold from options (failures before entities become unavailable)."""
        return int(
            self.config_entry.options.get(
                CONF_FAILURE_THRESHOLD, DEFAULT_FAILURE_THRESHOLD
            )
        )

    def _is_wifi_status_enabled(self) -> bool:
        """Return True if any WiFi status entity is enabled for this entry."""
        if not self.config_entry:
            return False
        wifi_keys = {
            "wifi_rssi",
            "wifi_sta_ip",
            "wifi_sta_gate",
            "wifi_sta_mask",
            "wifi_sta_dns",
        }
        entity_registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(
            entity_registry, self.config_entry.entry_id
        )
        for entry in entries:
            if not entry.unique_id:
                continue
            for key in wifi_keys:
                if entry.unique_id.endswith(f"_{key}"):
                    if entry.disabled_by is None:
                        return True
        return False

    @property
    def device_ip(self) -> str:
        """Get current device IP from config entry (supports dynamic IP updates)."""
        if self.config_entry:
            return self.config_entry.data.get(CONF_HOST, self._initial_device_ip)
        return self._initial_device_ip

    @property
    def device_port(self) -> int:
        """Get current device port from config entry (supports dynamic updates)."""
        if self.config_entry:
            return int(self.config_entry.data.get(CONF_PORT, self._initial_device_port))
        return self._initial_device_port

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data using library's get_device_status method with tiered polling.
        
        Tiered polling intervals (configurable per device):
        - Fast (base interval): ES.GetMode, ES.GetStatus, EM.GetStatus - real-time power
        - Medium: PV.GetStatus - solar data
        - Slow: Wifi.GetStatus, Bat.GetStatus - rarely changes
        """
        current_ip = self.device_ip
        current_port = self.device_port
        self.last_update_attempt_time = dt_util.now()
        _LOGGER.debug("Start polling device: %s:%s", current_ip, current_port)

        if self.udp_client.is_polling_paused(current_ip):
            _LOGGER.debug("Polling paused for device: %s, skipping update", current_ip)
            return self.data or {}

        # Determine which data types to fetch based on elapsed time
        current_time = time.monotonic()
        medium_interval = self._get_medium_interval()
        slow_interval = self._get_slow_interval()
        request_delay = self._get_request_delay()
        
        # PV data - medium interval, but only if device supports PV (Venus D only)
        include_pv = (
            self._supports_pv 
            and (current_time - self._last_pv_fetch) >= medium_interval
        )
        
        # WiFi and battery details - slow interval
        include_slow = (current_time - self._last_slow_fetch) >= slow_interval
        include_wifi = include_slow and self._is_wifi_status_enabled()
        
        # Get configured timeout
        request_timeout = self._get_request_timeout()
        
        _LOGGER.debug(
            "Polling tiers for %s: fast=always, pv=%s, wifi=%s, bat=%s",
            current_ip,
            include_pv,
            include_wifi,
            include_slow,
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
                include_em=True,  # Always fetch - fast tier
                include_bat=include_slow,
                delay_between_requests=request_delay,
                previous_status=self.data,  # Preserve values on partial failures
            )

            # Update last fetch times for successful fetches
            if include_pv:
                self._last_pv_fetch = current_time
            if include_slow:
                self._last_slow_fetch = current_time

            # Check if we actually got valid data
            has_fresh_data = device_status.get("has_fresh_data", True)
            device_mode = device_status.get("device_mode")
            battery_soc = device_status.get("battery_soc")
            battery_power = device_status.get("battery_power")
            battery_status = device_status.get("battery_status")
            pv_power = sum(
                device_status.get(key) or 0
                for key in ("pv1_power", "pv2_power", "pv3_power", "pv4_power")
            )
            em_total_power = device_status.get("em_total_power")
            wifi_rssi = device_status.get("wifi_rssi")
            bat_temp = device_status.get("bat_temp")

            has_valid_data = (
                device_mode not in (None, "Unknown", "unknown")
                or battery_soc is not None
                or battery_power is not None
                or battery_status not in (None, "Unknown")
                or pv_power != 0
                or em_total_power is not None
                or wifi_rssi is not None
                or bat_temp is not None
            )

            if not has_fresh_data:
                _LOGGER.warning(
                    "No fresh data received from device at %s - keeping previous values",
                    current_ip,
                )
                error_msg = f"No fresh data received from device at {current_ip}"
                raise TimeoutError(error_msg) from None  # noqa: TRY301

            if not has_valid_data:
                _LOGGER.warning(
                    "No valid data received from device at %s (device_mode=%s, soc=%s, power=%s) - connection failed",
                    current_ip,
                    device_mode or "Unknown",
                    battery_soc or 0,
                    battery_power or 0,
                )
                error_msg = f"No valid data received from device at {current_ip}"
                raise TimeoutError(error_msg) from None  # noqa: TRY301
            if device_mode in ("Unknown", "unknown"):
                _LOGGER.debug(
                    "Device %s reported device_mode=Unknown but other data is present (soc=%s, power=%s)",
                    current_ip,
                    battery_soc or 0,
                    battery_power or 0,
                )
            _LOGGER.debug(
                "Device %s poll done: SOC %s%%, Power %sW, Mode %s, Status %s (pv=%s, slow=%s)",
                current_ip,
                device_status.get("battery_soc"),
                device_status.get("battery_power"),
                device_status.get("device_mode"),
                device_status.get("battery_status"),
                include_pv,
                include_slow,
            )

            # Update success tracking
            self.last_update_success_time = dt_util.now()
            self.consecutive_failures = 0

            # Clear any existing connection issue on successful update
            self._clear_connection_issue()

            return device_status  # noqa: TRY300
        except (TimeoutError, OSError, ValueError) as err:
            # Connection failed - Scanner will detect IP changes and update config entry
            self.consecutive_failures += 1
            failure_threshold = self._get_failure_threshold()
            
            if self.consecutive_failures >= failure_threshold:
                _LOGGER.warning(
                    "Device %s status request failed (attempt #%d, threshold: %d): %s. "
                    "Entities will become unavailable. "
                    "Scanner will detect IP changes automatically",
                    current_ip,
                    self.consecutive_failures,
                    failure_threshold,
                    err,
                )
                self._create_connection_issue(str(err))
                # Mark update as failed so entities become unavailable
                raise UpdateFailed(
                    f"Polling failed for {current_ip} (attempt #{self.consecutive_failures}): {err}"
                ) from err
            
            # Below threshold - log warning but return cached data to keep entities available
            _LOGGER.warning(
                "Device %s status request failed (attempt #%d of %d): %s. "
                "Keeping entities available with cached data",
                current_ip,
                self.consecutive_failures,
                failure_threshold,
                err,
            )
            # Return cached data - entities stay available
            return self.data or {}

    def _issue_id(self) -> str:
        return f"cannot_connect_{self.config_entry.entry_id}"

    def _create_connection_issue(self, error: str) -> None:
        """Create a fixable connection issue for this entry."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(),
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="cannot_connect",
            translation_placeholders={"host": self.device_ip, "error": error},
            data={"entry_id": self.config_entry.entry_id},
        )

    def _clear_connection_issue(self) -> None:
        """Clear the connection issue if it exists."""
        issue_registry = ir.async_get(self.hass)
        issue_id = self._issue_id()
        if issue_registry.async_get_issue(DOMAIN, issue_id):
            issue_registry.async_delete(DOMAIN, issue_id)
