"""Broadcast discovery half of the Open API UDP client.

:class:`BroadcastDiscoveryMixin` carries everything that talks to the whole
subnet at once -- the broadcast sweep, the reply-to-device mapping and the
short-lived cache in front of both. :class:`.udp.MarstekUDPClient` mixes it
in, so the split is a file boundary rather than an API boundary: callers and
tests still reach these methods on the client.

Unicast exchanges, the socket and the listener task stay in ``udp.py``; the
declarations below name the parts of the client this half borrows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import TYPE_CHECKING, Any

from .command_builder import discover
from .const import DISCOVERY_TIMEOUT
from .device_info import build_device_info, non_empty_str
from .response_router import ResponseRouter
from .validators import (
    ValidationError,
    normalize_json_rpc_wire_message,
    validate_json_message,
)

_LOGGER = logging.getLogger(__name__)

# How often a broadcast sweep drains replies out of the response cache.
BROADCAST_DRAIN_INTERVAL: float = 0.1


class BroadcastDiscoveryMixin:
    """Broadcast sweeps and the discovery cache for the UDP client."""

    # Supplied by MarstekUDPClient.__init__.
    _port: int
    _router: ResponseRouter
    _discovery_cache: list[dict[str, Any]] | None
    _cache_timestamp: float
    _cache_duration: float

    if TYPE_CHECKING:
        # Implemented by MarstekUDPClient; declared so this half type checks
        # on its own.
        def loop_time(self) -> float: ...

        def _configured_loop(self) -> asyncio.AbstractEventLoop: ...

        def _ensure_listener(self) -> None: ...

        async def _async_broadcast_addresses(self) -> list[str]: ...

        async def _ensure_socket(self) -> socket.socket: ...

        async def _send_udp_message(
            self,
            message: str,
            target_ip: str,
            target_port: int,
            *,
            bypass_rate_limit: bool = False,
        ) -> None: ...

    def _is_cache_valid(self) -> bool:
        if self._discovery_cache is None:
            return False
        return (self.loop_time() - self._cache_timestamp) < self._cache_duration

    def clear_discovery_cache(self) -> None:
        self._discovery_cache = None
        self._cache_timestamp = 0

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

            broadcast_addresses = await self._async_broadcast_addresses()
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
                responses.extend(self._router.take_cached(request_id, since=start_time))
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
