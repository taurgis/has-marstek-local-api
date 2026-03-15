"""Low level UDP client implementation for pymarstek.

All outbound messages are validated before transmission to protect devices
from malformed requests. See validators.py for validation rules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast

from .command_builder import (
    discover,
    get_battery_status,
    get_em_status,
    get_es_mode,
    get_es_status,
    get_pv_status,
    get_wifi_status,
)
from .const import DEFAULT_UDP_PORT, DISCOVERY_TIMEOUT
from .data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
    parse_wifi_status_response,
)
from .network import PsutilModule, get_broadcast_addresses
from .validators import ValidationError, validate_json_message

_LOGGER = logging.getLogger(__name__)

_PSUTIL_AUTO = object()
psutil: PsutilModule | object | None = _PSUTIL_AUTO

# Rate limiting - minimum interval between requests to same device
MIN_REQUEST_INTERVAL: float = 0.3  # 300ms minimum between requests to same IP


def _new_command_stats() -> dict[str, Any]:
    """Create a new command stats bucket."""
    return {
        "total_attempts": 0,
        "total_success": 0,
        "total_timeouts": 0,
        "total_failures": 0,
        "last_success": None,
        "last_latency": None,
        "last_timeout": None,
        "last_error": None,
        "last_updated": None,
    }


def _build_discovered_device(result: dict[str, Any]) -> dict[str, Any]:
    """Build device info dict from discovery response."""
    device_ip = result.get("ip", "")
    return {
        "id": result.get("id", 0),
        "device_type": result.get("device", "Unknown"),
        "version": result.get("ver", 0),
        "wifi_name": result.get("wifi_name", ""),
        "ip": device_ip,
        "wifi_mac": result.get("wifi_mac", ""),
        "ble_mac": result.get("ble_mac", ""),
        "mac": result.get("wifi_mac") or result.get("ble_mac", ""),
        "model": result.get("device", "Unknown"),
        "firmware": str(result.get("ver", 0)),
    }


class MarstekUDPClient:
    """UDP client for communicating with Marstek devices.

    Features:
    - Request validation before transmission (see validators.py)
    - Rate limiting per device IP to prevent overwhelming devices
    - Polling pause/resume for coordinated device control
    - Discovery caching to reduce network traffic
    """

    def __init__(
        self,
        port: int = DEFAULT_UDP_PORT,
        *,
        bind_port: int | None = None,
    ) -> None:
        self._port = port
        self._bind_port = bind_port if bind_port is not None else port
        self._socket: socket.socket | None = None
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._response_cache: dict[int, dict[str, Any]] = {}
        self._listen_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._discovery_cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0
        self._cache_duration: float = 30.0

        self._local_send_ip: str = "0.0.0.0"
        self._polling_paused: dict[str, bool] = {}
        self._polling_lock: asyncio.Lock = asyncio.Lock()

        # Rate limiting: track last request time per device IP
        self._last_request_time: dict[str, float] = {}
        self._rate_limit_locks: dict[str, asyncio.Lock] = {}  # Per-IP locks
        self._rate_limit_meta_lock: asyncio.Lock = asyncio.Lock()  # For creating per-IP locks

        # Cleanup: max tracked IPs before cleanup
        self._max_tracked_ips: int = 100
        self._rate_limit_cleanup_threshold: float = 300.0  # 5 minutes

        # Response cache cleanup settings
        self._response_cache_max_size: int = 50
        self._response_cache_max_age: float = 30.0  # 30 seconds

        # Command diagnostics (per method, optional per device IP)
        self._command_stats: dict[str, dict[str, Any]] = {}
        self._command_stats_by_ip: dict[str, dict[str, dict[str, Any]]] = {}

    def _get_command_stats_bucket(
        self, method: str, *, device_ip: str | None = None
    ) -> dict[str, Any]:
        """Get or create a command stats bucket."""
        if device_ip is None:
            stats = self._command_stats.setdefault(method, _new_command_stats())
            return stats

        per_ip = self._command_stats_by_ip.setdefault(device_ip, {})
        stats = per_ip.setdefault(method, _new_command_stats())
        return stats

    def _record_command_result(
        self,
        method: str,
        *,
        device_ip: str | None,
        success: bool,
        timeout: bool,
        latency: float | None,
        error: str | None,
    ) -> None:
        """Record command outcome for diagnostics."""
        for bucket in (
            self._get_command_stats_bucket(method, device_ip=device_ip),
            self._get_command_stats_bucket(method, device_ip=None),
        ):
            bucket["total_attempts"] += 1
            if success:
                bucket["total_success"] += 1
            elif timeout:
                bucket["total_timeouts"] += 1
            else:
                bucket["total_failures"] += 1

            bucket["last_success"] = success
            bucket["last_latency"] = latency
            bucket["last_timeout"] = timeout
            bucket["last_error"] = error
            bucket["last_updated"] = time.time()

    def get_command_stats(self) -> dict[str, dict[str, Any]]:
        """Return snapshot of command stats for all methods."""
        return {method: dict(stats) for method, stats in self._command_stats.items()}

    def get_command_stats_for_ip(self, device_ip: str) -> dict[str, dict[str, Any]]:
        """Return snapshot of command stats for a specific device IP."""
        return {
            method: dict(stats)
            for method, stats in self._command_stats_by_ip.get(device_ip, {}).items()
        }

    async def async_setup(self) -> None:
        """Prepare the UDP socket."""
        if self._socket is not None:
            return

        self._loop = asyncio.get_running_loop()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", self._bind_port))
        self._socket = sock
        _LOGGER.debug("UDP client bound to %s:%s", sock.getsockname()[0], sock.getsockname()[1])

    async def async_cleanup(self) -> None:
        """Close the UDP socket and clear all caches."""
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listen_task
        if self._socket:
            self._socket.close()
            self._socket = None

        # Clear caches to prevent memory retention after cleanup
        self._pending_requests.clear()
        self._response_cache.clear()
        self._discovery_cache = None
        self._last_request_time.clear()
        self._rate_limit_locks.clear()
        self._polling_paused.clear()
        self._command_stats.clear()
        self._command_stats_by_ip.clear()

    async def _ensure_socket(self) -> socket.socket:
        """Ensure the UDP socket is initialized and return it."""
        if not self._socket:
            await self.async_setup()
        assert self._socket is not None
        return self._socket

    def _ensure_listener(self) -> None:
        """Ensure the response listener task is running."""
        if not self._listen_task or self._listen_task.done():
            loop = self._loop or asyncio.get_running_loop()
            self._listen_task = loop.create_task(self._listen_for_responses())

    def _is_cache_valid(self) -> bool:
        if self._discovery_cache is None:
            return False
        loop = self._loop or asyncio.get_running_loop()
        return (loop.time() - self._cache_timestamp) < self._cache_duration

    def clear_discovery_cache(self) -> None:
        self._discovery_cache = None
        self._cache_timestamp = 0

    def _get_broadcast_addresses(self) -> list[str]:
        if psutil is _PSUTIL_AUTO:
            return get_broadcast_addresses(logger=_LOGGER)
        if psutil is None:
            return get_broadcast_addresses(logger=_LOGGER, allow_import=False)
        return get_broadcast_addresses(
            psutil_module=cast(PsutilModule, psutil),
            logger=_LOGGER,
            allow_import=False,
        )

    async def _get_rate_limit_lock(self, target_ip: str) -> asyncio.Lock:
        """Get or create a per-IP rate limit lock."""
        async with self._rate_limit_meta_lock:
            if target_ip not in self._rate_limit_locks:
                self._rate_limit_locks[target_ip] = asyncio.Lock()
            return self._rate_limit_locks[target_ip]

    async def _cleanup_rate_limit_tracking(self) -> None:
        """Remove stale entries from rate limit tracking to prevent memory leaks."""
        loop = self._loop or asyncio.get_running_loop()
        current_time = loop.time()

        async with self._rate_limit_meta_lock:
            if len(self._last_request_time) <= self._max_tracked_ips:
                return

            # Remove entries older than cleanup threshold
            stale_ips = [
                ip for ip, last_time in self._last_request_time.items()
                if current_time - last_time > self._rate_limit_cleanup_threshold
            ]

            for ip in stale_ips:
                self._last_request_time.pop(ip, None)
                self._rate_limit_locks.pop(ip, None)
                self._command_stats_by_ip.pop(ip, None)

            if stale_ips:
                _LOGGER.debug("Cleaned up rate limit tracking for %d stale IPs", len(stale_ips))

    def _cleanup_response_cache(self) -> None:
        """Remove stale entries from response cache to prevent memory leaks.

        Called periodically during response listening to prevent unbounded growth
        from late responses or orphaned cache entries.
        """
        if not self._response_cache:
            return

        loop = self._loop or asyncio.get_running_loop()
        current_time = loop.time()

        # Remove entries older than max age
        stale_ids = [
            request_id for request_id, cached in self._response_cache.items()
            if current_time - cached.get("timestamp", 0) > self._response_cache_max_age
        ]

        for request_id in stale_ids:
            self._response_cache.pop(request_id, None)

        # If still too large, remove oldest entries
        if len(self._response_cache) > self._response_cache_max_size:
            sorted_entries = sorted(
                self._response_cache.items(),
                key=lambda x: x[1].get("timestamp", 0)
            )
            # Remove oldest half
            to_remove = len(self._response_cache) - self._response_cache_max_size // 2
            for request_id, _ in sorted_entries[:to_remove]:
                self._response_cache.pop(request_id, None)

            if to_remove > 0:
                _LOGGER.debug(
                    "Cleaned up %d stale response cache entries",
                    to_remove + len(stale_ids),
                )

    async def _enforce_rate_limit(self, target_ip: str) -> None:
        """Enforce minimum interval between requests to the same device.

        This prevents overwhelming Marstek devices which can be sensitive
        to rapid request bursts. Uses per-IP locks to avoid blocking
        requests to different devices.
        """
        loop = self._loop or asyncio.get_running_loop()

        # Get per-IP lock (creates one if needed)
        ip_lock = await self._get_rate_limit_lock(target_ip)

        async with ip_lock:
            current_time = loop.time()
            last_time = self._last_request_time.get(target_ip, 0)
            elapsed = current_time - last_time

            if elapsed < MIN_REQUEST_INTERVAL:
                wait_time = MIN_REQUEST_INTERVAL - elapsed
                _LOGGER.debug(
                    "Rate limiting: waiting %.2fs before request to %s",
                    wait_time,
                    target_ip,
                )
                await asyncio.sleep(wait_time)

            # Update last request time
            self._last_request_time[target_ip] = loop.time()

        # Periodically cleanup stale entries
        if len(self._last_request_time) > self._max_tracked_ips:
            await self._cleanup_rate_limit_tracking()

    async def _send_udp_message(
        self,
        message: str,
        target_ip: str,
        target_port: int,
        *,
        bypass_rate_limit: bool = False,
    ) -> None:
        sock = await self._ensure_socket()

        # Enforce rate limiting for non-broadcast addresses
        if (
            not bypass_rate_limit
            and target_ip not in ("255.255.255.255",)
            and not target_ip.endswith(".255")
        ):
            await self._enforce_rate_limit(target_ip)

        data = message.encode("utf-8")
        sock.sendto(data, (target_ip, target_port))
        _LOGGER.debug("Send: %s:%d | %s", target_ip, target_port, message)

    async def send_request(
        self,
        message: str,
        target_ip: str,
        target_port: int,
        timeout: float = 5.0,
        *,
        quiet_on_timeout: bool = False,
        validate: bool = True,
        bypass_rate_limit: bool = False,
    ) -> dict[str, Any]:
        """Send a request message and wait for response.

        Args:
            message: JSON command string to send
            target_ip: Target device IP address
            target_port: Target device port
            timeout: Response timeout in seconds
            quiet_on_timeout: If True, don't log warnings on timeout
            validate: If True, validate message before sending (default True).
                Set to False only if message was already validated.
            bypass_rate_limit: If True, skip per-device UDP throttling for this request

        Returns:
            Response dictionary from device

        Raises:
            ValidationError: If message validation fails and validate=True
            TimeoutError: If no response received within timeout
            ValueError: If message has no id field
        """
        await self._ensure_socket()

        # Validate message before sending to protect device
        if validate:
            try:
                command = validate_json_message(message)
            except ValidationError as err:
                # Safely try to extract method for logging context
                method_name = "unknown"
                try:
                    if message:
                        method_name = json.loads(message).get("method", "unknown")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass

                _LOGGER.error(
                    "Request validation failed for %s:%d [method=%s, field=%s]: %s",
                    target_ip,
                    target_port,
                    method_name,
                    err.field or "unknown",
                    err.message,
                )
                raise
            request_id = command["id"]
            method_name = str(command.get("method", "unknown"))
        else:
            try:
                message_obj = json.loads(message)
                request_id = message_obj["id"]
                method_name = str(message_obj.get("method", "unknown"))
            except (json.JSONDecodeError, KeyError) as exc:
                raise ValueError("Invalid message: missing id") from exc

        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._pending_requests[request_id] = future

        try:
            self._ensure_listener()

            request_started = time.time()
            await self._send_udp_message(
                message,
                target_ip,
                target_port,
                bypass_rate_limit=bypass_rate_limit,
            )
            _LOGGER.debug("Send request to %s:%d: %s", target_ip, target_port, message)
            response = await asyncio.wait_for(future, timeout=timeout)
            latency = time.time() - request_started
            self._record_command_result(
                method_name,
                device_ip=target_ip,
                success=True,
                timeout=False,
                latency=latency,
                error=None,
            )
            return response
        except TimeoutError as err:
            if not quiet_on_timeout:
                _LOGGER.warning("Request timeout: %s:%d", target_ip, target_port)
            self._record_command_result(
                method_name,
                device_ip=target_ip,
                success=False,
                timeout=True,
                latency=None,
                error="timeout",
            )
            raise TimeoutError(f"Request timeout to {target_ip}:{target_port}") from err
        except (OSError, ValueError) as err:
            self._record_command_result(
                method_name,
                device_ip=target_ip,
                success=False,
                timeout=False,
                latency=None,
                error=str(err),
            )
            raise
        finally:
            self._pending_requests.pop(request_id, None)

    async def _listen_for_responses(self) -> None:
        assert self._socket is not None
        loop = self._loop or asyncio.get_running_loop()
        cleanup_counter = 0
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)
                response_text = data.decode("utf-8")
                try:
                    response = json.loads(response_text)
                except json.JSONDecodeError:
                    response = {"raw": response_text}
                request_id = response.get("id") if isinstance(response, dict) else None
                _LOGGER.debug("Recv: %s:%d | %s", addr[0], addr[1], response)
                if request_id:
                    self._response_cache[request_id] = {
                        "response": response,
                        "addr": addr,
                        "timestamp": loop.time(),
                    }
                    future = self._pending_requests.pop(request_id, None)
                    if future and not future.done():
                        future.set_result(response)

                # Periodically cleanup response cache to prevent memory leaks
                cleanup_counter += 1
                if cleanup_counter >= 10:  # Every 10 responses
                    cleanup_counter = 0
                    self._cleanup_response_cache()
            except asyncio.CancelledError:
                break
            except OSError as err:
                _LOGGER.error("Error receiving UDP response: %s", err)
                await asyncio.sleep(1)

    async def send_broadcast_request(
        self,
        message: str,
        timeout: float = DISCOVERY_TIMEOUT,
        *,
        validate: bool = True,
    ) -> list[dict[str, Any]]:
        """Send a broadcast message and collect all responses within timeout.

        Args:
            message: JSON command string to broadcast
            timeout: Time to wait for responses in seconds
            validate: If True, validate message before sending (default True)

        Returns:
            List of response dictionaries from devices

        Raises:
            ValidationError: If message validation fails and validate=True
        """
        _LOGGER.debug("Starting broadcast discovery with timeout %ss", timeout)
        await self._ensure_socket()

        # Validate message before broadcasting to protect devices
        if validate:
            try:
                validate_json_message(message)
            except ValidationError as err:
                _LOGGER.error("Broadcast validation failed: %s", err.message)
                return []

        try:
            message_obj = json.loads(message)
            request_id = message_obj["id"]
        except (json.JSONDecodeError, KeyError) as exc:
            _LOGGER.error("Invalid message for broadcast: %s", exc)
            return []

        responses: list[dict[str, Any]] = []
        loop = self._loop or asyncio.get_running_loop()
        start_time = loop.time()

        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._pending_requests[request_id] = future

        try:
            self._ensure_listener()

            broadcast_addresses = self._get_broadcast_addresses()
            _LOGGER.debug("Broadcast addresses: %s on port %d", broadcast_addresses, self._port)
            for address in broadcast_addresses:
                await self._send_udp_message(message, address, self._port)

            while (loop.time() - start_time) < timeout:
                cached = self._response_cache.pop(request_id, None)
                if cached:
                    _LOGGER.debug("Received device response: %s", cached["response"])
                    responses.append(cached["response"])
                await asyncio.sleep(0.1)
        finally:
            self._pending_requests.pop(request_id, None)
        _LOGGER.debug("Broadcast discovery completed, found %d device(s)", len(responses))
        return responses

    async def discover_devices(self, use_cache: bool = True) -> list[dict[str, Any]]:
        """Discover Marstek devices on the network via broadcast."""
        _LOGGER.debug("Starting device discovery (use_cache=%s)", use_cache)
        if use_cache and self._is_cache_valid():
            assert self._discovery_cache is not None
            _LOGGER.debug("Using cached discovery data (%d devices)", len(self._discovery_cache))
            return self._discovery_cache.copy()

        devices: list[dict[str, Any]] = []
        seen_devices: set[str] = set()

        try:
            responses = await self.send_broadcast_request(discover())
        except OSError as err:
            _LOGGER.error("Device discovery failed: %s", err)
            responses = []

        loop = self._loop or asyncio.get_running_loop()

        for response in responses:
            result = response.get("result") if isinstance(response, dict) else None
            if not isinstance(result, dict):
                continue

            device_id = (
                result.get("ip")
                or result.get("ble_mac")
                or result.get("wifi_mac")
                or f"device_{int(loop.time())}_{hash(str(result)) % 10000}"
            )
            if device_id in seen_devices:
                continue
            seen_devices.add(device_id)

            devices.append(_build_discovered_device(result))

        self._discovery_cache = devices.copy()
        self._cache_timestamp = loop.time()
        _LOGGER.debug("Device discovery completed, found %d device(s)", len(devices))
        for device in devices:
            _LOGGER.debug("Found device: %s at %s", device.get("device_type"), device.get("ip"))
        return devices

    async def pause_polling(self, device_ip: str) -> None:
        async with self._polling_lock:
            self._polling_paused[device_ip] = True

    async def resume_polling(self, device_ip: str) -> None:
        async with self._polling_lock:
            self._polling_paused[device_ip] = False

    def is_polling_paused(self, device_ip: str) -> bool:
        return self._polling_paused.get(device_ip, False)

    async def send_request_with_polling_control(
        self,
        message: str,
        target_ip: str,
        target_port: int,
        timeout: float = 5.0,
        *,
        validate: bool = True,
    ) -> dict[str, Any]:
        """Send request while pausing polling to avoid concurrent traffic.

        Args:
            message: JSON command string to send
            target_ip: Target device IP address
            target_port: Target device port
            timeout: Response timeout in seconds
            validate: If True, validate message before sending (default True)

        Returns:
            Response dictionary from device

        Raises:
            ValidationError: If message validation fails and validate=True
        """
        await self.pause_polling(target_ip)
        try:
            return await self.send_request(
                message, target_ip, target_port, timeout, quiet_on_timeout=True, validate=validate
            )
        finally:
            await self.resume_polling(target_ip)

    async def get_device_status(
        self,
        device_ip: str,
        port: int = DEFAULT_UDP_PORT,
        timeout: float = 2.5,
        *,
        include_pv: bool = True,
        include_wifi: bool = True,
        include_em: bool = True,
        include_bat: bool = True,
        parallel_requests: bool = False,
        delay_between_requests: float = 2.0,
        previous_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get complete device status including battery, PV, WiFi, and EM data.

        Calls ES.GetMode for device mode, ES.GetStatus for battery power/status,
        and optionally PV.GetStatus, Wifi.GetStatus, EM.GetStatus, Bat.GetStatus.

        Args:
            device_ip: IP address of the device
            port: UDP port (default: DEFAULT_UDP_PORT)
            timeout: Request timeout in seconds
            include_pv: Whether to include PV status data
            include_wifi: Whether to include WiFi status (RSSI)
            include_em: Whether to include Energy Meter/CT data
            include_bat: Whether to include detailed battery data
            parallel_requests: If True, request all enabled APIs concurrently
                without delay between calls
            delay_between_requests: Delay between requests in seconds
            previous_status: Previous device status to preserve values when
                individual requests fail (prevents intermittent "Unknown" states)

        Returns:
            Dictionary with complete device status
        """
        es_mode_data: dict[str, Any] | None = None
        es_status_data: dict[str, Any] | None = None
        pv_status_data: dict[str, Any] | None = None
        wifi_status_data: dict[str, Any] | None = None
        em_status_data: dict[str, Any] | None = None
        bat_status_data: dict[str, Any] | None = None

        # Track if we've made a request (to know when to add delay)
        made_request = False
        # Track if any request returned data
        has_fresh_data = False

        async def _request_and_parse(
            command: str,
            parser: Callable[[dict[str, Any]], dict[str, Any]],
            *,
            success_log: Callable[[dict[str, Any]], None],
            failure_log: str,
            apply_delay: bool,
            bypass_rate_limit: bool,
        ) -> dict[str, Any] | None:
            """Send a request and parse response with shared error handling."""
            nonlocal made_request, has_fresh_data
            if apply_delay and made_request:
                await asyncio.sleep(delay_between_requests)
            try:
                response = await self.send_request(
                    command,
                    device_ip,
                    port,
                    timeout=timeout,
                    bypass_rate_limit=bypass_rate_limit,
                )
                parsed = parser(response)
                made_request = True
                has_fresh_data = True
                success_log(parsed)
                return parsed
            except (TimeoutError, OSError, ValueError) as err:
                _LOGGER.debug(failure_log, device_ip, err)
                return None

        def _log_es_mode(data: dict[str, Any]) -> None:
            _LOGGER.debug(
                "ES.GetMode parsed for %s: Mode=%s, GridPower=%sW",
                device_ip,
                data.get("device_mode"),
                data.get("ongrid_power"),
            )

        def _log_es_status(data: dict[str, Any]) -> None:
            _LOGGER.debug(
                "ES.GetStatus parsed for %s: SOC=%s%%, BattPower=%sW, Status=%s",
                device_ip,
                data.get("battery_soc"),
                data.get("battery_power"),
                data.get("battery_status"),
            )

        def _log_em_status(data: dict[str, Any]) -> None:
            _LOGGER.debug(
                "EM.GetStatus parsed for %s: CT=%s, TotalPower=%sW",
                device_ip,
                "Connected" if data.get("ct_connected") else "Not connected",
                data.get("em_total_power"),
            )

        def _log_pv_status(data: dict[str, Any]) -> None:
            _LOGGER.debug(
                "PV.GetStatus parsed for %s: PV1=%sW, PV2=%sW, PV3=%sW, PV4=%sW",
                device_ip,
                data.get("pv1_power"),
                data.get("pv2_power"),
                data.get("pv3_power"),
                data.get("pv4_power"),
            )

        def _log_wifi_status(data: dict[str, Any]) -> None:
            _LOGGER.debug(
                "Wifi.GetStatus parsed for %s: RSSI=%s dBm, SSID=%s",
                device_ip,
                data.get("wifi_rssi"),
                data.get("wifi_ssid"),
            )

        def _log_bat_status(data: dict[str, Any]) -> None:
            _LOGGER.debug(
                "Bat.GetStatus parsed for %s: Temp=%s°C, ChargFlag=%s, DischrgFlag=%s",
                device_ip,
                data.get("bat_temp"),
                data.get("bat_charg_flag"),
                data.get("bat_dischrg_flag"),
            )

        if parallel_requests:
            request_keys: list[str] = []
            request_tasks: list[asyncio.Task[dict[str, Any] | None]] = []

            def _schedule_request(
                key: str,
                command: str,
                parser: Callable[[dict[str, Any]], dict[str, Any]],
                success_log: Callable[[dict[str, Any]], None],
                failure_log: str,
            ) -> None:
                request_keys.append(key)
                request_tasks.append(
                    asyncio.create_task(
                        _request_and_parse(
                            command,
                            parser,
                            success_log=success_log,
                            failure_log=failure_log,
                            apply_delay=False,
                            bypass_rate_limit=True,
                        )
                    )
                )

            _schedule_request(
                "es_mode",
                get_es_mode(0),
                parse_es_mode_response,
                _log_es_mode,
                "ES.GetMode failed for %s: %s",
            )
            _schedule_request(
                "es_status",
                get_es_status(0),
                parse_es_status_response,
                _log_es_status,
                "ES.GetStatus failed for %s: %s",
            )
            if include_em:
                _schedule_request(
                    "em_status",
                    get_em_status(0),
                    parse_em_status_response,
                    _log_em_status,
                    "EM.GetStatus failed for %s: %s",
                )
            if include_pv:
                _schedule_request(
                    "pv_status",
                    get_pv_status(0),
                    parse_pv_status_response,
                    _log_pv_status,
                    "PV.GetStatus failed for %s: %s",
                )
            if include_wifi:
                _schedule_request(
                    "wifi_status",
                    get_wifi_status(0),
                    parse_wifi_status_response,
                    _log_wifi_status,
                    "Wifi.GetStatus failed for %s: %s",
                )
            if include_bat:
                _schedule_request(
                    "bat_status",
                    get_battery_status(0),
                    parse_bat_status_response,
                    _log_bat_status,
                    "Bat.GetStatus failed for %s: %s",
                )

            results = await asyncio.gather(*request_tasks)
            for key, result in zip(request_keys, results, strict=True):
                if key == "es_mode":
                    es_mode_data = result
                elif key == "es_status":
                    es_status_data = result
                elif key == "em_status":
                    em_status_data = result
                elif key == "pv_status":
                    pv_status_data = result
                elif key == "wifi_status":
                    wifi_status_data = result
                elif key == "bat_status":
                    bat_status_data = result
        else:
            # Get ES mode (device_mode, ongrid_power) - always fetched (fast tier)
            es_mode_data = await _request_and_parse(
                get_es_mode(0),
                parse_es_mode_response,
                success_log=_log_es_mode,
                failure_log="ES.GetMode failed for %s: %s",
                apply_delay=True,
                bypass_rate_limit=False,
            )

            # Get ES status (battery_power, battery_status) - always fetched (fast tier)
            es_status_data = await _request_and_parse(
                get_es_status(0),
                parse_es_status_response,
                success_log=_log_es_status,
                failure_log="ES.GetStatus failed for %s: %s",
                apply_delay=True,
                bypass_rate_limit=False,
            )

            # Get EM status (CT/energy meter) - always fetched (fast tier)
            if include_em:
                em_status_data = await _request_and_parse(
                    get_em_status(0),
                    parse_em_status_response,
                    success_log=_log_em_status,
                    failure_log="EM.GetStatus failed for %s: %s",
                    apply_delay=True,
                    bypass_rate_limit=False,
                )

            # Get PV status if requested (medium tier)
            if include_pv:
                pv_status_data = await _request_and_parse(
                    get_pv_status(0),
                    parse_pv_status_response,
                    success_log=_log_pv_status,
                    failure_log="PV.GetStatus failed for %s: %s",
                    apply_delay=True,
                    bypass_rate_limit=False,
                )

            # Get WiFi status (slow tier - RSSI signal strength)
            if include_wifi:
                wifi_status_data = await _request_and_parse(
                    get_wifi_status(0),
                    parse_wifi_status_response,
                    success_log=_log_wifi_status,
                    failure_log="Wifi.GetStatus failed for %s: %s",
                    apply_delay=True,
                    bypass_rate_limit=False,
                )

            # Get detailed battery status (slow tier - temperature, charge flags)
            if include_bat:
                bat_status_data = await _request_and_parse(
                    get_battery_status(0),
                    parse_bat_status_response,
                    success_log=_log_bat_status,
                    failure_log="Bat.GetStatus failed for %s: %s",
                    apply_delay=True,
                    bypass_rate_limit=False,
                )

        # Merge data (ES.GetStatus has priority for battery data)
        # Pass previous_status to preserve values when individual requests fail
        loop = self._loop or asyncio.get_running_loop()
        status = merge_device_status(
            es_mode_data=es_mode_data,
            es_status_data=es_status_data,
            pv_status_data=pv_status_data,
            wifi_status_data=wifi_status_data,
            em_status_data=em_status_data,
            bat_status_data=bat_status_data,
            device_ip=device_ip,
            last_update=loop.time(),
            previous_status=previous_status,
        )
        status["has_fresh_data"] = has_fresh_data
        return status
