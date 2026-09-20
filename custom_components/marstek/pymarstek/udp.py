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
from contextlib import AbstractAsyncContextManager, nullcontext, suppress
from typing import Any, cast

from ..firmware_profile import FirmwareProfile
from .command_builder import discover, get_es_mode
from .command_stats import CommandStats
from .const import (
    CMD_BATTERY_STATUS,
    CMD_DISCOVER,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
    CMD_WIFI_STATUS,
    DEFAULT_UDP_PORT,
    DISCOVERY_TIMEOUT,
)
from .data_parser import parse_es_mode_response
from .device_info import build_device_info, non_empty_str
from .device_status import fetch_device_status
from .network import (
    PsutilModule,
    async_resolve_host_ipv4,
    create_udp_socket,
    get_broadcast_addresses,
)
from .openapi_marks import OpenApiMarks
from .poll_gate import PollGate
from .response_router import ResponseRouter
from .throttle import DeviceThrottle
from .validators import (
    ValidationError,
    json_loads_strict,
    json_rpc_result_usable,
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
# FC41D Wi-Fi STA power-save and AP TIM buffering often drop or delay the
# first downlink unicast. RFC 1122 leaves UDP retransmission to the
# application. Wait this long for a reply before sending a second copy.
# 500 ms beat 300 ms on a live Venus E 150 Wi-Fi sweep (2/40 vs 7/40
# timeouts) without approaching the 1000 ms delay that stalled recovery.
# Ethernet replies are typically well under 200 ms, so LAN and dual-homed
# Ethernet IPs still get one datagram. Writes and unknown/reset-prone IPs
# stay one-shot.
UNICAST_RETRANSMIT_WAIT: float = 0.5
# How often broadcast discovery drains replies out of the response cache.
BROADCAST_DRAIN_INTERVAL: float = 0.1
# Upper bound on how long a unicast waits for a paused listener to resume.
# Comfortably longer than a full discovery sweep (DISCOVERY_TIMEOUT is 10s),
# short enough that a scan which died between pause and resume cannot wedge
# the coordinator for the lifetime of the entry.
RECEIVER_PAUSE_MAX_WAIT: float = 30.0
_ANONYMOUS_RESET_PRONE_OWNER = "*"
_READ_ONLY_UNICAST_METHODS: frozenset[str] = frozenset(
    {
        CMD_DISCOVER,
        CMD_BATTERY_STATUS,
        CMD_ES_STATUS,
        CMD_ES_MODE,
        CMD_PV_GET_STATUS,
        CMD_WIFI_STATUS,
        CMD_EM_STATUS,
    }
)


class _UnicastTimeoutError(TimeoutError):
    """Timeout for one unicast wait.

    ``retried`` is True when a silent-wait copy was already sent.
    """

    def __init__(self, retried: bool) -> None:
        super().__init__()
        self.retried = retried


LIMITED_BROADCAST_ADDRESS = "255.255.255.255"

# ES.GetMode instance ids observed in the wild. This integration prefers 0
# (Open API default) and falls back to 1 (vendor library default).
_ES_MODE_INSTANCE_IDS: tuple[int, ...] = (0, 1)



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
        self._closed: bool = False
        self._router: ResponseRouter = ResponseRouter(self.loop_time)
        self._listen_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._receiver_pause_count: int = 0
        self._in_flight_exchanges: int = 0
        self._exchange_gate: asyncio.Condition = asyncio.Condition()
        self._receiver_transition_lock: asyncio.Lock = asyncio.Lock()

        self._discovery_cache: list[dict[str, Any]] | None = None
        self._cache_timestamp: float = 0
        self._cache_duration: float = 30.0

        # Broadcast targets are exempt from per-IP throttling. Resolve them
        # from the interface table rather than guessing from the last octet:
        # a /23 host really can sit on x.x.1.255, and a /25 broadcast is
        # x.x.x.127. Refreshed whenever a broadcast send enumerates them.
        self._broadcast_addresses: frozenset[str] = frozenset(
            {LIMITED_BROADCAST_ADDRESS}
        )

        self._local_send_ip: str = "0.0.0.0"
        self._poll_gate: PollGate = PollGate()
        self._marks: OpenApiMarks = OpenApiMarks()

        # Per-device pacing and the locks that serialize their traffic
        self._throttle: DeviceThrottle = DeviceThrottle(self.loop_time)

        # Command diagnostics (per method, optional per device IP)
        self._command_stats: CommandStats = CommandStats()

        # Working ES.GetMode params.id per device IP (0 or 1)
        self._es_mode_device_ids: dict[str, int] = {}

    @property
    def bind_port(self) -> int:
        """Return the local UDP port this client is bound to."""
        return self._bind_port

    def get_command_stats_for_ip(self, device_ip: str) -> dict[str, dict[str, Any]]:
        """Return snapshot of command stats for a specific device IP."""
        return self._command_stats.snapshot_for_ip(device_ip)

    def loop_time(self) -> float:
        """Return the current clock reading of the configured loop."""
        return self._configured_loop().time()

    def _configured_loop(self) -> asyncio.AbstractEventLoop:
        """Return whatever loop the client was given, else the running one.

        Timing and task creation go through here so a caller that installed
        its own loop keeps its clock. Socket I/O uses :meth:`_event_loop`,
        which insists on a genuine event loop.
        """
        return self._loop or asyncio.get_running_loop()

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        """Return the client loop when it is a real event loop."""
        if isinstance(self._loop, asyncio.AbstractEventLoop):
            return self._loop
        return asyncio.get_running_loop()

    async def _resolve_unicast_ip(self, host: str) -> str:
        """Return the IPv4 address firmware will source replies from.

        Replies are matched on the sender's address, so a hostname has to be
        resolved before the waiter is keyed. An unresolvable name falls back
        to the host string and the exchange simply times out.
        """
        resolved = await async_resolve_host_ipv4(host)
        return resolved[0] if resolved else host

    async def _enter_unicast_exchange(self) -> None:
        """Wait until discovery listeners are running, then count this exchange.

        The wait is bounded. A scan that dies between pause and resume leaves
        the refcount raised, and an unbounded wait here would block every
        later unicast before its own timeout ever starts, so the coordinator
        would hang instead of failing. Past the bound the exchange proceeds:
        the worst case is one datagram sent while the listener is paused,
        which times out normally.
        """
        deadline = time.monotonic() + RECEIVER_PAUSE_MAX_WAIT
        async with self._exchange_gate:
            while self._receiver_pause_count > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _LOGGER.warning(
                        "Open API UDP listener still paused after %.0fs; "
                        "proceeding so polling cannot wedge",
                        RECEIVER_PAUSE_MAX_WAIT,
                    )
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._exchange_gate.wait(), remaining)
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
        self._closed = False
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
        self._closed = True
        self._receiver_pause_count = 0
        await self._stop_listener()
        if self._socket:
            self._socket.close()
            self._socket = None

        self._router.cancel_all()

        # Clear caches to prevent memory retention after cleanup
        self._discovery_cache = None
        self._throttle.clear()
        self._marks.clear()
        self._poll_gate.clear()
        async with self._exchange_gate:
            self._in_flight_exchanges = 0
            self._exchange_gate.notify_all()
        self._command_stats.clear()
        self._es_mode_device_ids.clear()

    async def _ensure_socket(self) -> socket.socket:
        """Ensure the UDP socket is initialized and return it.

        Never re-bind after cleanup. ``async_cleanup`` wakes everyone waiting
        on the exchange gate, and a waiter that then reached this method would
        bind a second socket into the ``SO_REUSEPORT`` group -- an orphan that
        outlives the client and steals datagrams from its replacement.
        Callers already treat OSError as a transport failure.
        """
        if self._closed:
            raise OSError("Marstek UDP client is closed")
        if not self._socket:
            await self.async_setup()
        assert self._socket is not None
        return self._socket

    def _ensure_listener(self) -> None:
        """Ensure the response listener task is running."""
        if self._receiver_pause_count > 0:
            return
        if not self._listen_task or self._listen_task.done():
            loop = self._configured_loop()
            self._listen_task = loop.create_task(self._listen_for_responses())

    def _is_cache_valid(self) -> bool:
        if self._discovery_cache is None:
            return False
        return (self.loop_time() - self._cache_timestamp) < self._cache_duration

    def clear_discovery_cache(self) -> None:
        self._discovery_cache = None
        self._cache_timestamp = 0

    def _get_broadcast_addresses(self) -> list[str]:
        if psutil is _PSUTIL_AUTO:
            addresses = get_broadcast_addresses(logger=_LOGGER)
        elif psutil is None:
            addresses = get_broadcast_addresses(logger=_LOGGER, allow_import=False)
        else:
            addresses = get_broadcast_addresses(
                psutil_module=cast(PsutilModule, psutil),
                logger=_LOGGER,
                allow_import=False,
            )
        self._broadcast_addresses = frozenset(addresses) | {
            LIMITED_BROADCAST_ADDRESS
        }
        return addresses

    def _is_broadcast_target(self, target_ip: str) -> bool:
        """Return True when *target_ip* is a broadcast address, not a device.

        Throttling a broadcast makes no sense (there is no single device to
        protect) but throttling a real host that happens to end in ``.255``
        is silently wrong, so match against the interface table instead of
        the last octet.
        """
        return target_ip in self._broadcast_addresses

    def set_openapi_reset_prone(
        self, device_ip: str, prone: bool, *, owner: str | None = None
    ) -> None:
        """Enable or disable per-request serialization for a device IP."""
        self._marks.set_reset_prone(device_ip, prone, owner=owner)

    def set_openapi_retransmit_safe(self, device_ip: str, enabled: bool) -> None:
        """Allow or deny Wi-Fi silent-wait retransmission for a device IP."""
        self._marks.set_retransmit_safe(device_ip, enabled)

    def is_openapi_retransmit_safe(self, device_ip: str) -> bool:
        """Return True when *device_ip* may receive extra read-only unicasts."""
        return self._marks.is_retransmit_safe(device_ip)

    def clear_openapi_reset_prone(
        self, device_ip: str, *, owner: str | None = None
    ) -> None:
        """Stop serializing Open API traffic for a device IP."""
        self._marks.clear_reset_prone(device_ip, owner=owner)

    def is_openapi_reset_prone(
        self, device_ip: str, *, owner: str | None = None
    ) -> bool:
        """Return True when *device_ip* is marked reset-prone for *owner*."""
        return self._marks.is_reset_prone(device_ip, owner=owner)

    def clear_openapi_reset_prone_owner(self, owner: str) -> None:
        """Drop every reset-prone mark owned by a config entry."""
        self._marks.clear_owner(owner)

    def transfer_openapi_reset_prone(
        self, old_ip: str, new_ip: str, *, owner: str
    ) -> None:
        """Move one owner's reset-prone mark when a device changes IP."""
        self._marks.transfer_reset_prone(old_ip, new_ip, owner=owner)

    async def _cleanup_rate_limit_tracking(self) -> None:
        """Forget devices quiet long enough to stop tracking, stats included."""
        for device_ip in await self._throttle.prune():
            self._command_stats.forget_ip(device_ip)

    async def _enforce_rate_limit(self, target_ip: str) -> None:
        """Enforce the minimum interval between requests to one device.

        Reset-prone Control builds get the stricter floor; everything else
        gets the normal one.
        """
        min_interval = (
            MIN_RESET_PRONE_REQUEST_INTERVAL
            if self._marks.is_reset_prone(target_ip)
            else MIN_REQUEST_INTERVAL
        )
        await self._throttle.wait_turn(target_ip, min_interval)
        if self._throttle.is_crowded():
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
        is_broadcast = self._is_broadcast_target(target_ip)

        # Enforce rate limiting for non-broadcast addresses. Reset-prone IPs
        # keep the floor even when a caller asks to bypass (GetDevice, retries).
        if is_broadcast:
            pass
        elif self._marks.is_reset_prone(target_ip) or not bypass_rate_limit:
            await self._enforce_rate_limit(target_ip)

        data = message.encode("utf-8")
        if not data:
            raise ValueError(
                "Refusing to send an empty UDP datagram; Control firmware "
                "freezes Open API on 0-byte packets"
            )
        # The socket is non-blocking, so a full send buffer would make
        # ``sock.sendto`` raise BlockingIOError instead of queueing.
        await self._event_loop().sock_sendto(sock, data, (target_ip, target_port))
        if not is_broadcast:
            # Stamp every datagram, including the ones that bypassed the
            # throttle. Otherwise a bypassing send (GetDevice, a Wi-Fi
            # retransmit) leaves the clock stale and the *next* throttled
            # request believes the device has been idle. Same clock as
            # ``_enforce_rate_limit``, which reads the stamp back.
            self._throttle.note_sent(target_ip)
        _LOGGER.debug("Send: %s:%d | %s", target_ip, target_port, message)

    def _wifi_reliability_enabled(self, target_ip: str, method_name: str) -> bool:
        """Return whether this unicast may use extra Wi-Fi copies.

        Unknown firmware stays one-shot. Writes stay one-shot even on
        known-safe firmware so ES.SetMode / SYS commands cannot double.
        """
        return (
            self.is_openapi_retransmit_safe(target_ip)
            and method_name in _READ_ONLY_UNICAST_METHODS
        )

    def _unicast_allows_retransmit(
        self, target_ip: str, method_name: str, timeout: float
    ) -> bool:
        """Return whether a silent first wait may be followed by a second send.

        Short unit-test timeouts skip the extra wait so they do not pay
        500 ms. The remaining wait uses the caller's timeout budget.
        """
        return (
            self._wifi_reliability_enabled(target_ip, method_name)
            and timeout > UNICAST_RETRANSMIT_WAIT
        )

    async def _wait_for_pending_response(
        self,
        future: asyncio.Future[dict[str, Any]],
        timeout: float,
    ) -> dict[str, Any]:
        """Wait for a pending reply without cancelling it on timeout.

        ``asyncio.wait_for`` cancels the inner future. A late FC41D reply
        after a Wi-Fi retransmission must still complete the same request.
        ``asyncio.wait`` does not cancel; see Python asyncio-task docs.
        """
        await asyncio.wait(
            {future},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if future.done():
            return future.result()
        raise TimeoutError

    async def _send_and_wait_unicast(
        self,
        message: str,
        target_ip: str,
        target_port: int,
        timeout: float,
        future: asyncio.Future[dict[str, Any]],
        *,
        bypass_rate_limit: bool,
        allow_retransmit: bool,
        method_name: str,
    ) -> tuple[dict[str, Any], bool]:
        """Send one unicast and wait, retransmitting only if the first wait is silent.

        The silent-wait copy stays inside *timeout* so one logical request
        cannot consume two full timeouts. Ethernet replies that arrive
        before 500 ms never send the copy.

        The deadline starts once the datagram is on the wire. Per-IP
        throttling can sleep up to a second before that (reset-prone floor),
        and charging that sleep to the caller's budget would time out a
        device that in fact answered promptly.
        """
        retried = False
        await self._send_udp_message(
            message,
            target_ip,
            target_port,
            bypass_rate_limit=bypass_rate_limit,
        )
        deadline = time.monotonic() + timeout
        if allow_retransmit:
            try:
                return (
                    await self._wait_for_pending_response(
                        future, UNICAST_RETRANSMIT_WAIT
                    ),
                    False,
                )
            except TimeoutError:
                retried = True
                _LOGGER.debug(
                    "No UDP reply from %s:%d for %s within %.2fs; retransmitting",
                    target_ip,
                    target_port,
                    method_name,
                    UNICAST_RETRANSMIT_WAIT,
                )
                await self._send_udp_message(
                    message,
                    target_ip,
                    target_port,
                    bypass_rate_limit=True,
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if future.done():
                return future.result(), retried
            raise _UnicastTimeoutError(retried)
        try:
            return (
                await self._wait_for_pending_response(future, remaining),
                retried,
            )
        except TimeoutError as err:
            raise _UnicastTimeoutError(retried) from err

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
            timeout: Overall wait for a matching reply, including any
                silent-wait copy
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
        if method_name == CMD_BATTERY_STATUS and self._marks.is_reset_prone(target_ip):
            raise ValidationError(
                "Bat.GetStatus is blocked on reset-prone firmware",
                field="method",
            )
        # Reset-prone Control builds cannot take overlapping Open API
        # requests, so those IPs exchange under a per-device lock. Everything
        # else runs the same exchange unguarded.
        guard: AbstractAsyncContextManager[Any, None] = (
            await self._throttle.io_lock(target_ip)
            if self._marks.is_reset_prone(target_ip)
            else nullcontext()
        )
        async with guard:
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
            pending_ip = await self._resolve_unicast_ip(target_ip)
            request_id, future = self._router.track(request_id, device_ip=pending_ip)

            try:
                self._ensure_listener()

                request_started = time.monotonic()
                _LOGGER.debug(
                    "Send request to %s:%d: %s",
                    target_ip,
                    target_port,
                    message,
                )
                retried = False
                try:
                    response, retried = await self._send_and_wait_unicast(
                        message,
                        target_ip,
                        target_port,
                        timeout,
                        future,
                        bypass_rate_limit=bypass_rate_limit,
                        allow_retransmit=self._unicast_allows_retransmit(
                            target_ip, method_name, timeout
                        ),
                        method_name=method_name,
                    )
                except TimeoutError as err:
                    if not quiet_on_timeout:
                        _LOGGER.warning(
                            "Request timeout: %s:%d [%s]",
                            target_ip,
                            target_port,
                            method_name,
                        )
                    self._command_stats.record(
                        method_name,
                        device_ip=target_ip,
                        success=False,
                        timeout=True,
                        latency=None,
                        error="timeout",
                        retransmitted=isinstance(err, _UnicastTimeoutError)
                        and err.retried,
                    )
                    raise TimeoutError(
                        f"Request timeout to {target_ip}:{target_port}"
                    ) from err
                latency = time.monotonic() - request_started
                if retried:
                    _LOGGER.debug(
                        "Got UDP reply from %s:%d for %s after retransmit (%.0f ms)",
                        target_ip,
                        target_port,
                        method_name,
                        latency * 1000,
                    )
                # A JSON-RPC error is a delivered reply, not a success.
                # Counting it as one hides "method not found" storms behind a
                # 100% success rate in diagnostics.
                error = response.get("error")
                self._command_stats.record(
                    method_name,
                    device_ip=target_ip,
                    success=error is None,
                    timeout=False,
                    latency=latency,
                    error=None if error is None else str(error),
                    retransmitted=retried,
                )
                return response

            except TimeoutError:
                raise
            except (OSError, ValueError) as err:
                self._command_stats.record(
                    method_name,
                    device_ip=target_ip,
                    success=False,
                    timeout=False,
                    latency=None,
                    error=str(err),
                )
                raise
            finally:
                self._router.drop_waiter(request_id, device_ip=pending_ip)
        finally:
            await self._exit_unicast_exchange()

    async def _listen_for_responses(self) -> None:
        assert self._socket is not None
        loop = self._configured_loop()
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
                try:
                    response_text = data.decode("utf-8")
                except UnicodeDecodeError:
                    _LOGGER.debug(
                        "Ignoring malformed UTF-8 UDP datagram from %s:%d",
                        addr[0],
                        addr[1],
                    )
                    continue
                try:
                    response = json_loads_strict(response_text)
                except json.JSONDecodeError:
                    response = {"raw": response_text}
                raw_id = response.get("id") if isinstance(response, dict) else None
                request_id = json_rpc_wire_id(raw_id)
                _LOGGER.debug("Recv: %s:%d | %s", addr[0], addr[1], response)
                if request_id is not None:
                    self._router.deliver(request_id, response, addr)

                # Periodically cleanup response cache to prevent memory leaks
                cleanup_counter += 1
                if cleanup_counter >= 10:  # Every 10 responses
                    cleanup_counter = 0
                    self._router.evict_stale()
            except asyncio.CancelledError:
                break
            except OSError as err:
                _LOGGER.error("Error receiving UDP response: %s", err)
                await asyncio.sleep(1)
            except Exception:
                _LOGGER.exception(
                    "Unexpected error in Open API UDP listener; continuing"
                )
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
            request_id, _future = self._router.track(request_id)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            _LOGGER.error("Invalid message for broadcast: %s", exc)
            return []

        responses: list[dict[str, Any]] = []
        loop = self._configured_loop()
        start_time = loop.time()

        try:
            self._ensure_listener()

            broadcast_addresses = self._get_broadcast_addresses()
            _LOGGER.debug("Broadcast addresses: %s on port %d", broadcast_addresses, self._port)
            for address in broadcast_addresses:
                await self._send_udp_message(message, address, self._port)

            # Drain *after* each sleep, including the final one: a reply that
            # lands in the last interval is already cached, and breaking out
            # of the loop without a last drain would silently discard it.
            deadline = start_time + timeout
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(BROADCAST_DRAIN_INTERVAL, remaining))
                responses.extend(
                    self._router.take_cached(request_id, since=start_time)
                )
        finally:
            self._router.drop_waiter(request_id)
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

        loop = self._configured_loop()

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

            src = ""
            if isinstance(response, dict):
                src = non_empty_str(response.get("src"))
            devices.append(build_device_info(result, src=src))

        self._discovery_cache = devices.copy()
        self._cache_timestamp = loop.time()
        _LOGGER.debug("Device discovery completed, found %d device(s)", len(devices))
        for device in devices:
            _LOGGER.debug("Found device: %s at %s", device.get("device_type"), device.get("ip"))
        return devices

    async def begin_poll_cycle(self, device_ip: str) -> bool:
        """Mark a coordinator poll cycle as running.

        Returns False when polling is paused so the coordinator can skip.
        """
        return await self._poll_gate.begin_cycle(device_ip)

    async def end_poll_cycle(self, device_ip: str) -> None:
        """Mark a coordinator poll cycle finished so paused writers proceed."""
        await self._poll_gate.end_cycle(device_ip)

    async def pause_polling(self, device_ip: str) -> None:
        """Hold off polls for a device and wait for the running cycle."""
        await self._poll_gate.pause(device_ip)

    async def resume_polling(self, device_ip: str) -> None:
        """Release one pause taken by :meth:`pause_polling`."""
        await self._poll_gate.resume(device_ip)

    def is_polling_paused(self, device_ip: str) -> bool:
        """Return True while a writer holds polling paused for a device."""
        return self._poll_gate.is_paused(device_ip)

    def _es_mode_instance_order(self, device_ip: str) -> tuple[int, ...]:
        """Return ES.GetMode instance ids, cached winner first."""
        cached = self._es_mode_device_ids.get(device_ip)
        if cached == 1:
            return (1, 0)
        return _ES_MODE_INSTANCE_IDS

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
            if not json_rpc_result_usable(response):
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
        """Return a merged status snapshot for one device.

        See :func:`.device_status.fetch_device_status` for how the individual
        Open API reads are chosen, paced and merged.
        """
        return await fetch_device_status(
            self,
            device_ip,
            port,
            timeout,
            include_pv=include_pv,
            include_wifi=include_wifi,
            include_em=include_em,
            include_bat=include_bat,
            parallel_requests=parallel_requests,
            delay_between_requests=delay_between_requests,
            previous_status=previous_status,
            profile=profile,
        )
