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

from ..firmware_profile import FirmwareProfile, extract_discovery_version
from .command_builder import (
    discover,
    get_battery_status,
    get_em_status,
    get_es_mode,
    get_es_status,
    get_pv_status,
    get_wifi_status,
)
from .const import CMD_BATTERY_STATUS, DEFAULT_UDP_PORT, DISCOVERY_TIMEOUT
from .data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
    parse_wifi_status_response,
)
from .network import PsutilModule, create_udp_socket, get_broadcast_addresses
from .validators import (
    ValidationError,
    json_rpc_wire_id,
    normalize_json_rpc_wire_message,
    validate_json_message,
)

_LOGGER = logging.getLogger(__name__)

_PSUTIL_AUTO = object()
psutil: PsutilModule | object | None = _PSUTIL_AUTO

# Rate limiting - minimum interval between requests to same device
MIN_REQUEST_INTERVAL: float = 0.3  # 300ms minimum between requests to same IP
# Control firmware below 150 is heap-sensitive; keep a stricter floor even when
# a caller asks to bypass the normal 300ms throttle (GetDevice, retries).
MIN_RESET_PRONE_REQUEST_INTERVAL: float = 1.0
_ANONYMOUS_RESET_PRONE_OWNER = "*"


def _is_broadcast_address(target_ip: str) -> bool:
    """Return True for limited-broadcast and x.x.x.255 subnet broadcasts."""
    return target_ip in {"255.255.255.255"} or target_ip.endswith(".255")

# ES.GetMode instance ids observed in the wild. This integration prefers 0
# (Open API default) and falls back to 1 (vendor library default).
_ES_MODE_INSTANCE_IDS: tuple[int, ...] = (0, 1)


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
    version = extract_discovery_version(result)
    return {
        "id": result.get("id", 0),
        "device_type": result.get("device", "Unknown"),
        "version": version,
        "wifi_name": result.get("wifi_name", ""),
        "ip": device_ip,
        "wifi_mac": result.get("wifi_mac", ""),
        "ble_mac": result.get("ble_mac", ""),
        "mac": result.get("wifi_mac") or result.get("ble_mac", ""),
        "model": result.get("device", "Unknown"),
        "firmware": "" if version is None else str(version),
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
        self._pending_requests: dict[
            int | tuple[str, int], asyncio.Future[dict[str, Any]]
        ] = {}
        self._response_cache: dict[int | tuple[str, int], dict[str, Any]] = {}
        self._listen_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._receiver_pause_count: int = 0
        self._in_flight_exchanges: int = 0
        self._exchange_gate: asyncio.Condition = asyncio.Condition()
        self._receiver_transition_lock: asyncio.Lock = asyncio.Lock()

        self._discovery_cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0
        self._cache_duration: float = 30.0

        self._local_send_ip: str = "0.0.0.0"
        self._polling_paused: dict[str, bool] = {}
        self._polling_pause_counts: dict[str, int] = {}
        self._poll_cycle_counts: dict[str, int] = {}
        self._poll_cycle_idle: dict[str, asyncio.Event] = {}
        self._polling_lock: asyncio.Lock = asyncio.Lock()
        self._reset_prone_ips: set[str] = set()
        self._reset_prone_owners: dict[str, set[str]] = {}
        self._device_io_locks: dict[str, asyncio.Lock] = {}

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

        # Working ES.GetMode params.id per device IP (0 or 1)
        self._es_mode_device_ids: dict[str, int] = {}

    @property
    def bind_port(self) -> int:
        """Return the local UDP port this client is bound to."""
        return self._bind_port

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

    def _pending_key(
        self, wire_id: int, device_ip: str | None
    ) -> int | tuple[str, int]:
        """Return the pending-request map key for a unicast or broadcast id."""
        if device_ip is None:
            return wire_id
        return (device_ip, wire_id)

    def _track_pending(
        self, request_id: Any, *, device_ip: str | None = None
    ) -> tuple[int, asyncio.Future[dict[str, Any]]]:
        """Register a pending request using the firmware's uint16 JSON-RPC id."""
        wire_id = json_rpc_wire_id(request_id)
        if wire_id is None:
            raise ValueError("Invalid message: missing id")
        key = self._pending_key(wire_id, device_ip)
        existing = self._pending_requests.get(key)
        if existing is not None and not existing.done():
            target = f" for {device_ip}" if device_ip else ""
            raise ValueError(f"Duplicate pending JSON-RPC id {wire_id}{target}")
        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._pending_requests[key] = future
        return wire_id, future

    def _pop_pending_future(
        self, request_id: int, *, source_ip: str | None = None
    ) -> asyncio.Future[dict[str, Any]] | None:
        """Resolve a pending future for a unicast reply or broadcast cache."""
        if source_ip is not None:
            future = self._pending_requests.pop((source_ip, request_id), None)
            if future is not None:
                return future
        future = self._pending_requests.pop(request_id, None)
        if future is not None:
            return future
        matches = [
            key
            for key in self._pending_requests
            if isinstance(key, tuple) and key[1] == request_id
        ]
        if len(matches) == 1:
            return self._pending_requests.pop(matches[0], None)
        return None

    def _pop_cached_responses_for_id(self, request_id: int) -> list[dict[str, Any]]:
        """Take cached replies for a JSON-RPC id from every source IP."""
        responses: list[dict[str, Any]] = []
        for key in list(self._response_cache):
            matches_id = key == request_id or (
                isinstance(key, tuple) and len(key) == 2 and key[1] == request_id
            )
            if not matches_id:
                continue
            cached = self._response_cache.pop(key)
            response = cached.get("response")
            if isinstance(response, dict):
                responses.append(response)
        return responses

    async def _enter_unicast_exchange(self) -> None:
        """Wait until discovery listeners are running, then count this exchange."""
        async with self._exchange_gate:
            while self._receiver_pause_count > 0:
                await self._exchange_gate.wait()
            self._in_flight_exchanges += 1

    async def _exit_unicast_exchange(self) -> None:
        """Mark a unicast exchange finished so discovery can pause the listener."""
        async with self._exchange_gate:
            if self._in_flight_exchanges > 0:
                self._in_flight_exchanges -= 1
            if self._in_flight_exchanges == 0:
                self._exchange_gate.notify_all()

    async def async_setup(self) -> None:
        """Prepare the UDP socket."""
        if self._socket is not None:
            return

        self._loop = asyncio.get_running_loop()

        sock = create_udp_socket(
            bind_port=self._bind_port,
            broadcast=True,
            fallback_ephemeral=False,
            logger=_LOGGER,
        )
        self._socket = sock
        _LOGGER.debug(
            "UDP client bound to %s:%s", sock.getsockname()[0], sock.getsockname()[1]
        )

    async def async_pause_receiver(self) -> None:
        """Stop the background UDP listener without closing the socket.

        Broadcast discovery binds the Open API port on a new socket. Pause
        this listener so ``SO_REUSEPORT`` does not steal those replies.
        Nested pauses are ref-counted so a config-flow scan that overlaps
        the scanner does not resume too early. Unicast GetDevice must reuse
        this client rather than pausing; pause does not unbind.

        In-flight unicast exchanges finish before the listener stops, and new
        unicasts wait until resume, so discovery does not send into a socket
        with no receiver.

        Nested pauses wait on a transition lock until the first pause has
        actually stopped the listener. Cancellation rolls the refcount back
        so a cancelled scan cannot leave unicasts blocked forever.
        """
        async with self._receiver_transition_lock:
            self._receiver_pause_count += 1
            if self._receiver_pause_count > 1:
                return
            try:
                async with self._exchange_gate:
                    while self._in_flight_exchanges > 0:
                        await self._exchange_gate.wait()
                await self._stop_listener()
            except BaseException:
                self._receiver_pause_count -= 1
                if self._receiver_pause_count == 0:
                    async with self._exchange_gate:
                        self._exchange_gate.notify_all()
                    if self._socket is not None:
                        self._ensure_listener()
                raise

    async def async_resume_receiver(self) -> None:
        """Restart the background UDP listener if the socket is open."""
        async with self._receiver_transition_lock:
            if self._receiver_pause_count <= 0:
                return
            self._receiver_pause_count -= 1
            if self._receiver_pause_count > 0:
                return
            async with self._exchange_gate:
                self._exchange_gate.notify_all()
            if self._socket is None:
                return
            self._ensure_listener()

    async def _stop_listener(self) -> None:
        """Cancel the background UDP listener if it is running."""
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._listen_task
        self._listen_task = None

    async def async_cleanup(self) -> None:
        """Close the UDP socket and clear all caches."""
        self._receiver_pause_count = 0
        await self._stop_listener()
        if self._socket:
            self._socket.close()
            self._socket = None

        pending = list(self._pending_requests.values())
        self._pending_requests.clear()
        for future in pending:
            if not future.done():
                future.cancel()

        # Clear caches to prevent memory retention after cleanup
        self._response_cache.clear()
        self._discovery_cache = None
        self._last_request_time.clear()
        self._rate_limit_locks.clear()
        self._device_io_locks.clear()
        self._reset_prone_ips.clear()
        self._reset_prone_owners.clear()
        self._polling_paused.clear()
        self._polling_pause_counts.clear()
        self._poll_cycle_counts.clear()
        for idle in self._poll_cycle_idle.values():
            idle.set()
        self._poll_cycle_idle.clear()
        async with self._exchange_gate:
            self._in_flight_exchanges = 0
            self._exchange_gate.notify_all()
        self._command_stats.clear()
        self._command_stats_by_ip.clear()
        self._es_mode_device_ids.clear()

    async def _ensure_socket(self) -> socket.socket:
        """Ensure the UDP socket is initialized and return it."""
        if not self._socket:
            await self.async_setup()
        assert self._socket is not None
        return self._socket

    def _ensure_listener(self) -> None:
        """Ensure the response listener task is running."""
        if self._receiver_pause_count > 0:
            return
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

    async def _get_device_io_lock(self, target_ip: str) -> asyncio.Lock:
        """Get or create a per-IP lock for reset-prone unicast exchanges."""
        async with self._rate_limit_meta_lock:
            if target_ip not in self._device_io_locks:
                self._device_io_locks[target_ip] = asyncio.Lock()
            return self._device_io_locks[target_ip]

    def set_openapi_reset_prone(
        self, device_ip: str, prone: bool, *, owner: str | None = None
    ) -> None:
        """Enable or disable per-request serialization for a device IP.

        Marks are reference-counted by *owner* (config entry id) so a
        SETUP_RETRY IP change cannot leave a stale mark that later serializes
        an unrelated 150+ device that reused the address.
        """
        owner_key = owner or _ANONYMOUS_RESET_PRONE_OWNER
        owners = self._reset_prone_owners.setdefault(device_ip, set())
        if prone:
            owners.add(owner_key)
            self._reset_prone_ips.add(device_ip)
            return
        owners.discard(owner_key)
        if not owners:
            self._reset_prone_owners.pop(device_ip, None)
            self._reset_prone_ips.discard(device_ip)

    def clear_openapi_reset_prone(
        self, device_ip: str, *, owner: str | None = None
    ) -> None:
        """Stop serializing Open API traffic for a device IP."""
        if owner is None:
            self._reset_prone_owners.pop(device_ip, None)
            self._reset_prone_ips.discard(device_ip)
            return
        self.set_openapi_reset_prone(device_ip, False, owner=owner)

    def transfer_openapi_reset_prone(
        self, old_ip: str, new_ip: str, *, owner: str
    ) -> None:
        """Move one owner's reset-prone mark when a device changes IP."""
        if old_ip == new_ip:
            return
        owners = self._reset_prone_owners.get(old_ip, set())
        marked = (
            owner in owners
            or _ANONYMOUS_RESET_PRONE_OWNER in owners
            or (old_ip in self._reset_prone_ips and not owners)
        )
        if not marked:
            return
        self.set_openapi_reset_prone(old_ip, False, owner=owner)
        if _ANONYMOUS_RESET_PRONE_OWNER in self._reset_prone_owners.get(old_ip, set()):
            self.set_openapi_reset_prone(
                old_ip, False, owner=_ANONYMOUS_RESET_PRONE_OWNER
            )
        elif old_ip in self._reset_prone_ips and old_ip not in self._reset_prone_owners:
            self.clear_openapi_reset_prone(old_ip)
        self.set_openapi_reset_prone(new_ip, True, owner=owner)

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
                self._device_io_locks.pop(ip, None)
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
            min_interval = (
                MIN_RESET_PRONE_REQUEST_INTERVAL
                if target_ip in self._reset_prone_ips
                else MIN_REQUEST_INTERVAL
            )

            if elapsed < min_interval:
                wait_time = min_interval - elapsed
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

        # Enforce rate limiting for non-broadcast addresses. Reset-prone IPs
        # keep the floor even when a caller asks to bypass (GetDevice, retries).
        if not _is_broadcast_address(target_ip) and (
            target_ip in self._reset_prone_ips or not bypass_rate_limit
        ):
            await self._enforce_rate_limit(target_ip)

        data = message.encode("utf-8")
        if not data:
            raise ValueError(
                "Refusing to send an empty UDP datagram; Control firmware "
                "freezes Open API on 0-byte packets"
            )
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
                validate_json_message(message)
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

        message, request_id, method_name = normalize_json_rpc_wire_message(message)
        if method_name == CMD_BATTERY_STATUS and target_ip in self._reset_prone_ips:
            raise ValidationError(
                "Bat.GetStatus is blocked on reset-prone firmware",
                field="method",
            )
        if target_ip in self._reset_prone_ips:
            lock = await self._get_device_io_lock(target_ip)
            async with lock:
                return await self._exchange_request(
                    message,
                    request_id,
                    method_name,
                    target_ip,
                    target_port,
                    timeout,
                    quiet_on_timeout=quiet_on_timeout,
                    bypass_rate_limit=bypass_rate_limit,
                )
        return await self._exchange_request(
            message,
            request_id,
            method_name,
            target_ip,
            target_port,
            timeout,
            quiet_on_timeout=quiet_on_timeout,
            bypass_rate_limit=bypass_rate_limit,
        )

    async def _exchange_request(
        self,
        message: str,
        request_id: int,
        method_name: str,
        target_ip: str,
        target_port: int,
        timeout: float,
        *,
        quiet_on_timeout: bool,
        bypass_rate_limit: bool,
    ) -> dict[str, Any]:
        """Send one normalized request and wait for its matching response."""
        await self._enter_unicast_exchange()
        try:
            request_id, future = self._track_pending(request_id, device_ip=target_ip)

            try:
                self._ensure_listener()

                request_started = time.time()
                await self._send_udp_message(
                    message,
                    target_ip,
                    target_port,
                    bypass_rate_limit=bypass_rate_limit,
                )
                _LOGGER.debug(
                    "Send request to %s:%d: %s", target_ip, target_port, message
                )
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
                raise TimeoutError(
                    f"Request timeout to {target_ip}:{target_port}"
                ) from err
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
                self._pending_requests.pop((target_ip, request_id), None)
        finally:
            await self._exit_unicast_exchange()

    async def _listen_for_responses(self) -> None:
        assert self._socket is not None
        loop = self._loop or asyncio.get_running_loop()
        cleanup_counter = 0
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._socket, 4096)
                if not data:
                    _LOGGER.debug(
                        "Ignoring empty UDP datagram from %s:%d",
                        addr[0],
                        addr[1],
                    )
                    continue
                response_text = data.decode("utf-8")
                try:
                    response = json.loads(response_text)
                except json.JSONDecodeError:
                    response = {"raw": response_text}
                raw_id = response.get("id") if isinstance(response, dict) else None
                request_id = json_rpc_wire_id(raw_id)
                _LOGGER.debug("Recv: %s:%d | %s", addr[0], addr[1], response)
                if request_id is not None:
                    self._response_cache[(addr[0], request_id)] = {
                        "response": response,
                        "addr": addr,
                        "timestamp": loop.time(),
                    }
                    future = self._pop_pending_future(request_id, source_ip=addr[0])
                    if (
                        future is None
                        and isinstance(raw_id, int)
                        and not isinstance(raw_id, bool)
                        and raw_id != request_id
                    ):
                        future = self._pop_pending_future(raw_id, source_ip=addr[0])
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

        if validate:
            try:
                validate_json_message(message)
            except ValidationError as err:
                _LOGGER.error("Broadcast validation failed: %s", err.message)
                return []

        try:
            message, request_id, _method_name = normalize_json_rpc_wire_message(message)
            request_id, _future = self._track_pending(request_id)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            _LOGGER.error("Invalid message for broadcast: %s", exc)
            return []

        responses: list[dict[str, Any]] = []
        loop = self._loop or asyncio.get_running_loop()
        start_time = loop.time()

        try:
            self._ensure_listener()

            broadcast_addresses = self._get_broadcast_addresses()
            _LOGGER.debug("Broadcast addresses: %s on port %d", broadcast_addresses, self._port)
            for address in broadcast_addresses:
                await self._send_udp_message(message, address, self._port)

            while (loop.time() - start_time) < timeout:
                responses.extend(self._pop_cached_responses_for_id(request_id))
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

    def _poll_cycle_idle_event(self, device_ip: str) -> asyncio.Event:
        """Return the idle event for a device poll cycle, creating it if needed."""
        event = self._poll_cycle_idle.get(device_ip)
        if event is None:
            event = asyncio.Event()
            event.set()
            self._poll_cycle_idle[device_ip] = event
        return event

    async def begin_poll_cycle(self, device_ip: str) -> bool:
        """Mark a coordinator poll cycle as running.

        Returns False when polling is paused so the coordinator can skip.
        """
        async with self._polling_lock:
            if self._polling_paused.get(device_ip, False):
                return False
            count = self._poll_cycle_counts.get(device_ip, 0) + 1
            self._poll_cycle_counts[device_ip] = count
            self._poll_cycle_idle_event(device_ip).clear()
            return True

    async def end_poll_cycle(self, device_ip: str) -> None:
        """Mark a coordinator poll cycle finished so paused writers can proceed."""
        async with self._polling_lock:
            count = self._poll_cycle_counts.get(device_ip, 0) - 1
            if count <= 0:
                self._poll_cycle_counts.pop(device_ip, None)
                self._poll_cycle_idle_event(device_ip).set()
                return
            self._poll_cycle_counts[device_ip] = count

    async def pause_polling(self, device_ip: str) -> None:
        async with self._polling_lock:
            count = self._polling_pause_counts.get(device_ip, 0) + 1
            self._polling_pause_counts[device_ip] = count
            self._polling_paused[device_ip] = True
            idle = self._poll_cycle_idle_event(device_ip)
        try:
            await idle.wait()
        except BaseException:
            await self.resume_polling(device_ip)
            raise

    async def resume_polling(self, device_ip: str) -> None:
        async with self._polling_lock:
            count = self._polling_pause_counts.get(device_ip, 0) - 1
            if count <= 0:
                self._polling_pause_counts.pop(device_ip, None)
                self._polling_paused[device_ip] = False
                return
            self._polling_pause_counts[device_ip] = count

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

    def _es_mode_instance_order(self, device_ip: str) -> tuple[int, ...]:
        """Return ES.GetMode instance ids, cached winner first."""
        cached = self._es_mode_device_ids.get(device_ip)
        if cached == 1:
            return (1, 0)
        return _ES_MODE_INSTANCE_IDS

    @staticmethod
    def _es_mode_response_usable(response: dict[str, Any]) -> bool:
        """Return whether GetMode produced a JSON-RPC result object.

        A result dict — even empty — is our historical success path. Retry the
        other instance id only on transport failure, a JSON-RPC error, or a
        missing/non-dict result (the vendor library's id=1 probe).
        """
        if "error" in response:
            return False
        return isinstance(response.get("result"), dict)

    async def fetch_es_mode(
        self,
        device_ip: str,
        port: int = DEFAULT_UDP_PORT,
        timeout: float = 2.5,
        *,
        profile: FirmwareProfile | None = None,
        bypass_rate_limit: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch ES.GetMode, preferring instance id 0 then falling back to 1.

        This integration's Open API default is ``id=0``. Some firmwares and the
        vendor library answer ``id=1`` instead. Probe ``0`` first, then ``1``,
        and cache the working id so later polls send one request.
        """
        last_error: Exception | None = None
        for instance_id in self._es_mode_instance_order(device_ip):
            try:
                response = await self.send_request(
                    get_es_mode(instance_id),
                    device_ip,
                    port,
                    timeout=timeout,
                    bypass_rate_limit=bypass_rate_limit,
                )
            except (TimeoutError, OSError, ValueError) as err:
                last_error = err
                _LOGGER.debug(
                    "ES.GetMode id=%s failed for %s: %s",
                    instance_id,
                    device_ip,
                    err,
                )
                continue
            if not self._es_mode_response_usable(response):
                _LOGGER.debug(
                    "ES.GetMode id=%s returned no usable result for %s: %s",
                    instance_id,
                    device_ip,
                    response,
                )
                continue
            parsed = parse_es_mode_response(response, profile)
            self._es_mode_device_ids[device_ip] = instance_id
            return parsed

        self._es_mode_device_ids.pop(device_ip, None)
        if last_error is not None:
            _LOGGER.debug("ES.GetMode failed for %s: %s", device_ip, last_error)
        return None

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
        profile: FirmwareProfile | None = None,
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

        def _parse_es_status(response: dict[str, Any]) -> dict[str, Any]:
            return parse_es_status_response(response, profile)

        def _parse_pv_status(response: dict[str, Any]) -> dict[str, Any]:
            return parse_pv_status_response(response, profile)

        def _parse_em_status(response: dict[str, Any]) -> dict[str, Any]:
            return parse_em_status_response(response, profile)

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

        async def _request_es_mode(
            *,
            apply_delay: bool,
            bypass_rate_limit: bool,
        ) -> dict[str, Any] | None:
            """Fetch ES.GetMode with instance-id fallback and shared logging."""
            nonlocal made_request, has_fresh_data
            if apply_delay and made_request:
                await asyncio.sleep(delay_between_requests)
            parsed = await self.fetch_es_mode(
                device_ip,
                port,
                timeout,
                profile=profile,
                bypass_rate_limit=bypass_rate_limit,
            )
            if parsed is None:
                _LOGGER.debug("ES.GetMode failed for %s: no usable result", device_ip)
                return None
            made_request = True
            has_fresh_data = True
            _log_es_mode(parsed)
            return parsed

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

            request_keys.append("es_mode")
            request_tasks.append(
                asyncio.create_task(
                    _request_es_mode(apply_delay=False, bypass_rate_limit=True)
                )
            )
            _schedule_request(
                "es_status",
                get_es_status(0),
                _parse_es_status,
                _log_es_status,
                "ES.GetStatus failed for %s: %s",
            )
            if include_em:
                _schedule_request(
                    "em_status",
                    get_em_status(0),
                    _parse_em_status,
                    _log_em_status,
                    "EM.GetStatus failed for %s: %s",
                )
            if include_pv:
                _schedule_request(
                    "pv_status",
                    get_pv_status(0),
                    _parse_pv_status,
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
            es_mode_data = await _request_es_mode(
                apply_delay=True,
                bypass_rate_limit=False,
            )

            # Get ES status (battery_power, battery_status) - always fetched (fast tier)
            es_status_data = await _request_and_parse(
                get_es_status(0),
                _parse_es_status,
                success_log=_log_es_status,
                failure_log="ES.GetStatus failed for %s: %s",
                apply_delay=True,
                bypass_rate_limit=False,
            )

            # Get EM status (CT/energy meter) - always fetched (fast tier)
            if include_em:
                em_status_data = await _request_and_parse(
                    get_em_status(0),
                    _parse_em_status,
                    success_log=_log_em_status,
                    failure_log="EM.GetStatus failed for %s: %s",
                    apply_delay=True,
                    bypass_rate_limit=False,
                )

            # Get PV status if requested (medium tier)
            if include_pv:
                pv_status_data = await _request_and_parse(
                    get_pv_status(0),
                    _parse_pv_status,
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
