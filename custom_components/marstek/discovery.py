"""Local UDP discovery for Marstek devices.

This module provides a workaround for pymarstek's discovery issues:
- Uses ID 0 for discovery (device echoes back same ID)
- Filters echoed requests (messages without 'result' key)
- Properly handles broadcast on all network interfaces
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import Iterable
from typing import Any, Protocol

from .const import DEFAULT_UDP_PORT
from .pymarstek import ValidationError, discover, json_loads_strict
from .pymarstek.device_info import build_device_info, non_empty_str
from .pymarstek.network import (
    async_resolve_host_ipv4,
    create_udp_socket,
    get_broadcast_addresses,
    is_loopback_host,
    udp_source_matches_host,
)

_LOGGER = logging.getLogger(__name__)

# Discovery settings
DISCOVERY_TIMEOUT = 10.0  # Total discovery timeout in seconds
DISCOVERY_METHOD = "Marstek.GetDevice"


def _normalize_ip(ip: str) -> str:
    """Normalize an IPv4 address by removing leading zeros from each octet."""
    parts = ip.split(".")
    if len(parts) != 4:
        return ip

    try:
        return ".".join(str(int(part)) for part in parts)
    except ValueError:
        return ip


def _build_discovery_message() -> bytes:
    """Build discovery request payload."""
    request = {
        "id": 0,
        "method": DISCOVERY_METHOD,
        "params": {"ble_mac": "0"},
    }
    return json.dumps(request).encode("utf-8")


def _build_device_info(
    result: dict[str, Any],
    device_ip: str,
    device_port: int,
    *,
    src: str = "",
) -> dict[str, Any]:
    """Build device info dict from discovery response result."""
    return build_device_info(result, ip=device_ip, port=device_port, src=src)


def _is_echo_response(response: Any) -> bool:
    """Check if a response is an echo of our request (not a valid device response).

    A datagram is whatever landed on the port, so the payload may be any JSON
    value. ``5``, ``null`` and ``true`` decode to objects that ``in`` cannot
    look inside, and the sweep must treat them as noise rather than raise.
    """
    if not isinstance(response, dict):
        return False
    # Valid device response must have 'result' key
    # Echo/request has 'method' and 'params' but no 'result'
    return "result" not in response and "method" in response and "params" in response


def _get_broadcast_addresses() -> list[str]:
    """Get broadcast addresses for all network interfaces.

    Blocking: imports psutil and reads the interface table. Reach it through
    :func:`_async_broadcast_addresses` rather than calling it on the loop.
    """
    return get_broadcast_addresses(logger=_LOGGER)


async def _async_broadcast_addresses(
    broadcast_addresses: Iterable[str] | None,
) -> list[str]:
    """Return the sweep targets without blocking the event loop.

    A caller that already knows the addresses (the Home Assistant layer asks
    the network integration as well) passes them in. Otherwise the interface
    table is read in the executor, because Home Assistant reports an import
    made on the event loop as a blocking call.
    https://developers.home-assistant.io/docs/asyncio_blocking_operations/
    """
    if broadcast_addresses is not None:
        return list(broadcast_addresses)
    return await asyncio.get_running_loop().run_in_executor(
        None, _get_broadcast_addresses
    )


class DeviceInfoUDPClient(Protocol):
    """UDP client that can send a unicast Open API request."""

    async def send_request(
        self,
        message: str,
        target_ip: str,
        target_port: int,
        timeout: float = 5.0,
        *,
        quiet_on_timeout: bool = False,
        bypass_rate_limit: bool = False,
    ) -> dict[str, Any]:
        """Send a request and wait for the matching response."""
        ...


def _device_info_from_response(
    response: dict[str, Any],
    host: str,
    port: int,
) -> dict[str, Any] | None:
    """Parse a GetDevice payload into device info, or None if invalid."""
    if _is_echo_response(response):
        _LOGGER.debug("Filtered echo from %s", host)
        return None
    if not _is_valid_device_response(response):
        _LOGGER.debug("Invalid device response from %s: %s", host, response)
        return None

    result = response["result"]
    if not isinstance(result, dict):
        return None
    return _build_device_info(
        result,
        _normalize_ip(result.get("ip", host)),
        port,
        src=non_empty_str(response.get("src")),
    )


def _is_valid_device_response(response: Any) -> bool:
    """Check if response contains valid device info.

    Total for the same reason as :func:`_is_echo_response`: the sweep hands
    this whatever the datagram decoded to, not only JSON objects.
    """
    if not isinstance(response, dict):
        return False
    if "result" not in response:
        return False
    result = response["result"]
    if not isinstance(result, dict):
        return False
    # Valid device response should have at least one identifier
    return any(key in result for key in ["device", "ip", "ble_mac", "wifi_mac"])


def _normalize_discovery_ports(
    ports: Iterable[int] | None,
    *,
    fallback_port: int,
) -> list[int]:
    """Normalize and deduplicate discovery ports."""
    candidates = list(ports) if ports is not None else [fallback_port]

    normalized: list[int] = []
    for candidate in candidates:
        try:
            port = int(candidate)
        except (TypeError, ValueError):
            continue
        if 1 <= port <= 65535 and port not in normalized:
            normalized.append(port)

    if not normalized:
        normalized.append(fallback_port)
    return normalized


async def discover_devices(
    timeout: float = DISCOVERY_TIMEOUT,
    port: int = DEFAULT_UDP_PORT,
    ports: Iterable[int] | None = None,
    *,
    broadcast_addresses: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Discover Marstek devices on the local network via UDP broadcast.

    This is a workaround for pymarstek's discovery issues:
    - Uses ID 0 which devices expect
    - Filters echoed requests
    - Properly validates device responses

    Args:
        timeout: Discovery timeout in seconds
        port: UDP port to use
        ports: Optional explicit list of UDP ports to scan
        broadcast_addresses: Sweep targets to use instead of reading the
            interface table here

    Returns:
        List of discovered device dictionaries
    """
    scan_ports = _normalize_discovery_ports(ports, fallback_port=port)
    _LOGGER.debug(
        "Starting local device discovery (timeout=%ss, ports=%s)",
        timeout,
        scan_ports,
    )

    # Bind one socket per scan port. Firmware replies to the device listen
    # port, so a probe sent from 30000 will miss a device configured on 30003.
    # Official protocol: https://static-eu.marstekenergy.com/ems/resource/agreement/MarstekDeviceOpenApi.pdf
    sockets: list[tuple[int, socket.socket]] = []
    bind_error: OSError | None = None
    for scan_port in scan_ports:
        try:
            sock = create_udp_socket(
                bind_port=scan_port,
                broadcast=True,
                fallback_ephemeral=True,
                logger=_LOGGER,
            )
        except OSError as err:
            bind_error = err
            _LOGGER.error("Failed to bind UDP socket on port %s: %s", scan_port, err)
            continue
        sockets.append((scan_port, sock))

    if not sockets:
        if bind_error is not None:
            raise bind_error
        raise OSError("Failed to bind UDP socket")

    loop = asyncio.get_running_loop()

    # Build discovery request with ID 0 (required by Marstek devices)
    message = _build_discovery_message()

    # Get all broadcast addresses
    broadcast_addrs = await _async_broadcast_addresses(broadcast_addresses)
    _LOGGER.debug("Broadcast addresses: %s", broadcast_addrs)

    for scan_port, sock in sockets:
        for addr in broadcast_addrs:
            try:
                await loop.sock_sendto(sock, message, (addr, scan_port))
                _LOGGER.debug("Sent discovery to %s:%d", addr, scan_port)
            except OSError as err:
                _LOGGER.warning("Failed to send to %s:%d: %s", addr, scan_port, err)

    devices: list[dict[str, Any]] = []
    seen_ips: set[str] = set()
    echoes_filtered = 0
    start_time = loop.time()

    # One outstanding receive per socket, all awaited together. Walking the
    # sockets in turn spent up to half a second on a quiet port while a later
    # one already had a reply queued, and overran ``timeout`` by that much per
    # extra port. ``asyncio.wait_for`` also cancels the receive it times out,
    # so a datagram delivered in that window was dropped -- UDP gives no
    # redelivery (RFC 768). ``asyncio.wait`` does not cancel.
    receivers: dict[asyncio.Task[Any], tuple[int, socket.socket]] = {
        asyncio.ensure_future(loop.sock_recvfrom(sock, 4096)): (scan_port, sock)
        for scan_port, sock in sockets
    }
    deadline = start_time + timeout

    try:
        while receivers:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            done, _pending = await asyncio.wait(
                set(receivers),
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break
            for task in done:
                scan_port, sock = receivers.pop(task)
                try:
                    data, addr = task.result()
                except TimeoutError:
                    continue
                except OSError as err:
                    _LOGGER.error(
                        "Socket error during discovery on port %s: %s",
                        scan_port,
                        err,
                    )
                    continue

                # Re-arm before parsing so a burst of replies is not missed
                # while this one is decoded.
                receivers[asyncio.ensure_future(loop.sock_recvfrom(sock, 4096))] = (
                    scan_port,
                    sock,
                )

                sender_ip: str = addr[0]
                sender_port = int(addr[1])

                try:
                    response = json_loads_strict(data.decode("utf-8"))
                except UnicodeDecodeError:
                    _LOGGER.debug(
                        "Invalid UTF-8 from %s:%d", sender_ip, sender_port
                    )
                    continue
                except json.JSONDecodeError:
                    _LOGGER.debug("Invalid JSON from %s:%d", sender_ip, sender_port)
                    continue

                if _is_echo_response(response):
                    echoes_filtered += 1
                    _LOGGER.debug("Filtered echo from %s:%d", sender_ip, sender_port)
                    continue

                if not _is_valid_device_response(response):
                    _LOGGER.debug(
                        "Invalid device response from %s:%d: %s",
                        sender_ip,
                        sender_port,
                        response,
                    )
                    continue

                result = response["result"]
                device_ip = _normalize_ip(result.get("ip", sender_ip))
                if device_ip in seen_ips:
                    _LOGGER.debug("Duplicate device at %s, skipping", device_ip)
                    continue

                seen_ips.add(device_ip)
                src = non_empty_str(response.get("src"))
                device = _build_device_info(result, device_ip, sender_port, src=src)
                devices.append(device)
                _LOGGER.info(
                    "Discovered device: %s at %s (BLE MAC: %s)",
                    device["device_type"],
                    device["ip"],
                    device["ble_mac"],
                )
    finally:
        for task in receivers:
            task.cancel()
        if receivers:
            await asyncio.gather(*receivers, return_exceptions=True)
        for _, sock in sockets:
            sock.close()

    _LOGGER.debug(
        "Discovery complete: found %d device(s), filtered %d echo(es)",
        len(devices),
        echoes_filtered,
    )

    return devices


async def _get_device_info_via_client(
    udp_client: DeviceInfoUDPClient,
    host: str,
    port: int,
    timeout: float,
) -> dict[str, Any] | None:
    """Query GetDevice on an already-bound UDP client.

    Firmware replies to the listen port. A second ``SO_REUSEPORT`` bind never
    sees that reply: Linux hashes it onto the socket that already owns the
    port, even if that listener is paused. Reuse the pooled client instead.
    """
    _LOGGER.debug(
        "Querying device info from %s:%d via pooled UDP client", host, port
    )
    try:
        response = await udp_client.send_request(
            discover(),
            host,
            port,
            timeout=timeout,
            quiet_on_timeout=True,
            bypass_rate_limit=True,
        )
    except TimeoutError:
        _LOGGER.warning("No valid response from device at %s:%d", host, port)
        return None
    except (OSError, ValueError, ValidationError) as err:
        _LOGGER.error("Socket error querying %s:%d: %s", host, port, err)
        return None

    if not isinstance(response, dict):
        _LOGGER.warning("No valid response from device at %s:%d", host, port)
        return None

    device = _device_info_from_response(response, host, port)
    if device is None:
        _LOGGER.warning("No valid response from device at %s:%d", host, port)
        return None

    _LOGGER.info(
        "Got device info: %s at %s (BLE MAC: %s)",
        device["device_type"],
        device["ip"],
        device["ble_mac"],
    )
    return device


async def get_device_info(
    host: str,
    port: int = DEFAULT_UDP_PORT,
    timeout: float = 5.0,
    *,
    udp_client: DeviceInfoUDPClient | None = None,
) -> dict[str, Any] | None:
    """Query a specific Marstek device for its info.

    Sends Marstek.GetDevice directly to the specified IP and returns device info.
    When *udp_client* is provided, the request is sent on that client so a
    second same-port bind cannot steal the reply.

    Args:
        host: Device IP address
        port: UDP port (default 30000)
        timeout: Response timeout in seconds
        udp_client: Existing client bound to this listen port, if any

    Returns:
        Device info dict or None if no response/invalid response
    """
    if udp_client is not None:
        return await _get_device_info_via_client(udp_client, host, port, timeout)

    _LOGGER.debug("Querying device info from %s:%d", host, port)

    # Same-host (loopback) devices already occupy the Open API port, so send
    # from an ephemeral port. Remote devices must be reached from that port.
    bind_port = 0 if is_loopback_host(host) else port
    try:
        sock = create_udp_socket(
            bind_port=bind_port,
            broadcast=True,
            fallback_ephemeral=True,
            logger=_LOGGER,
        )
    except OSError as err:
        _LOGGER.error("Failed to bind UDP socket for %s:%d: %s", host, port, err)
        return None

    # Build request
    message = _build_discovery_message()

    loop = asyncio.get_running_loop()

    # Resolve once, before the receive loop. Matching a hostname against each
    # datagram's sender used to run a blocking ``socket.getaddrinfo`` inside
    # the event loop, on every packet.
    expected_sources = await async_resolve_host_ipv4(host)

    try:
        # Send request directly to device
        await loop.sock_sendto(sock, message, (host, port))
        _LOGGER.debug("Sent GetDevice request to %s:%d", host, port)

        # Wait for response
        start_time = loop.time()
        while (loop.time() - start_time) < timeout:
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 4096),
                    timeout=min(0.5, timeout - (loop.time() - start_time)),
                )

                sender_ip, _ = addr
                if not udp_source_matches_host(
                    str(sender_ip), host, resolved=expected_sources
                ):
                    _LOGGER.debug(
                        "Ignoring GetDevice reply from %s while querying %s",
                        sender_ip,
                        host,
                    )
                    continue

                try:
                    response = json_loads_strict(data.decode("utf-8"))
                except UnicodeDecodeError:
                    _LOGGER.debug("Invalid UTF-8 from %s", sender_ip)
                    continue
                except json.JSONDecodeError:
                    _LOGGER.debug("Invalid JSON from %s", sender_ip)
                    continue

                device = _device_info_from_response(response, host, port)
                if device is None:
                    continue

                _LOGGER.info(
                    "Got device info: %s at %s (BLE MAC: %s)",
                    device["device_type"],
                    device["ip"],
                    device["ble_mac"],
                )
                return device

            except TimeoutError:
                continue

    except OSError as err:
        _LOGGER.error("Socket error querying %s:%d: %s", host, port, err)
    finally:
        sock.close()

    _LOGGER.warning("No valid response from device at %s:%d", host, port)
    return None
