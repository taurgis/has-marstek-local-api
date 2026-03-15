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
from typing import Any

from .const import DEFAULT_UDP_PORT
from .pymarstek.network import get_broadcast_addresses

_LOGGER = logging.getLogger(__name__)

# Discovery settings
DISCOVERY_TIMEOUT = 10.0  # Total discovery timeout in seconds
DISCOVERY_METHOD = "Marstek.GetDevice"


def _build_discovery_message() -> bytes:
    """Build discovery request payload."""
    request = {
        "id": 0,
        "method": DISCOVERY_METHOD,
        "params": {"ble_mac": "0"},
    }
    return json.dumps(request).encode("utf-8")


def _build_device_info(result: dict[str, Any], device_ip: str, device_port: int) -> dict[str, Any]:
    """Build device info dict from discovery response result."""
    return {
        "id": result.get("id", 0),
        "device_type": result.get("device", "Unknown"),
        "version": result.get("ver", 0),
        "wifi_name": result.get("wifi_name", ""),
        "ip": device_ip,
        "port": device_port,
        "wifi_mac": result.get("wifi_mac", ""),
        "ble_mac": result.get("ble_mac", ""),
        "mac": result.get("wifi_mac") or result.get("ble_mac", ""),
        "model": result.get("device", "Unknown"),
        "firmware": str(result.get("ver", 0)),
    }


def _is_echo_response(response: dict[str, Any]) -> bool:
    """Check if a response is an echo of our request (not a valid device response)."""
    # Valid device response must have 'result' key
    # Echo/request has 'method' and 'params' but no 'result'
    return "result" not in response and "method" in response and "params" in response


def _get_broadcast_addresses() -> list[str]:
    """Get broadcast addresses for all network interfaces."""
    return get_broadcast_addresses(logger=_LOGGER)


def _is_valid_device_response(response: dict[str, Any]) -> bool:
    """Check if response contains valid device info."""
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

    Returns:
        List of discovered device dictionaries
    """
    scan_ports = _normalize_discovery_ports(ports, fallback_port=port)
    _LOGGER.debug(
        "Starting local device discovery (timeout=%ss, ports=%s)",
        timeout,
        scan_ports,
    )

    # Create UDP socket with broadcast support
    # Bind to an ephemeral port to avoid conflicts with the shared UDP client
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)

    try:
        sock.bind(("0.0.0.0", 0))
        _LOGGER.debug("Socket bound to %s:%d", *sock.getsockname())
    except OSError as err:
        _LOGGER.error("Failed to bind UDP socket: %s", err)
        sock.close()
        raise

    loop = asyncio.get_running_loop()

    # Build discovery request with ID 0 (required by Marstek devices)
    message = _build_discovery_message()

    # Get all broadcast addresses
    broadcast_addrs = _get_broadcast_addresses()
    _LOGGER.debug("Broadcast addresses: %s", broadcast_addrs)

    # Send discovery broadcasts
    for addr in broadcast_addrs:
        for target_port in scan_ports:
            try:
                await loop.sock_sendto(sock, message, (addr, target_port))
                _LOGGER.debug("Sent discovery to %s:%d", addr, target_port)
            except OSError as err:
                _LOGGER.warning("Failed to send to %s:%d: %s", addr, target_port, err)

    # Collect responses
    devices: list[dict[str, Any]] = []
    seen_ips: set[str] = set()
    echoes_filtered = 0
    start_time = loop.time()

    while (loop.time() - start_time) < timeout:
        try:
            data, addr = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 4096),
                timeout=0.5,
            )

            # addr is tuple[str, int] for IPv4
            sender_ip: str = addr[0]
            sender_port = int(addr[1])

            try:
                response = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                _LOGGER.debug("Invalid JSON from %s:%d", sender_ip, sender_port)
                continue

            # Filter echoed requests
            if _is_echo_response(response):
                echoes_filtered += 1
                _LOGGER.debug("Filtered echo from %s:%d", sender_ip, sender_port)
                continue

            # Validate device response
            if not _is_valid_device_response(response):
                _LOGGER.debug(
                    "Invalid device response from %s:%d: %s",
                    sender_ip,
                    sender_port,
                    response,
                )
                continue

            result = response["result"]
            device_ip = result.get("ip", sender_ip)

            # Skip duplicates
            if device_ip in seen_ips:
                _LOGGER.debug("Duplicate device at %s, skipping", device_ip)
                continue

            seen_ips.add(device_ip)

            # Build device info dict (compatible with pymarstek format)
            device = _build_device_info(result, device_ip, sender_port)
            devices.append(device)
            _LOGGER.info(
                "Discovered device: %s at %s (BLE MAC: %s)",
                device["device_type"],
                device["ip"],
                device["ble_mac"],
            )

        except TimeoutError:
            # No response in this interval, continue waiting
            continue
        except OSError as err:
            _LOGGER.error("Socket error during discovery: %s", err)
            break

    sock.close()

    _LOGGER.debug(
        "Discovery complete: found %d device(s), filtered %d echo(es)",
        len(devices),
        echoes_filtered,
    )

    return devices


async def get_device_info(
    host: str,
    port: int = DEFAULT_UDP_PORT,
    timeout: float = 5.0,
) -> dict[str, Any] | None:
    """Query a specific Marstek device for its info.

    Sends Marstek.GetDevice directly to the specified IP and returns device info.

    Args:
        host: Device IP address
        port: UDP port (default 30000)
        timeout: Response timeout in seconds

    Returns:
        Device info dict or None if no response/invalid response
    """
    _LOGGER.debug("Querying device info from %s:%d", host, port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setblocking(False)

    # Bind to an ephemeral port to avoid conflicts with the shared UDP client
    sock.bind(("0.0.0.0", 0))

    # Build request
    message = _build_discovery_message()

    loop = asyncio.get_running_loop()

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

                try:
                    response = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    _LOGGER.debug("Invalid JSON from %s", sender_ip)
                    continue

                # Skip echoes
                if _is_echo_response(response):
                    _LOGGER.debug("Filtered echo from %s", sender_ip)
                    continue

                # Validate response
                if not _is_valid_device_response(response):
                    _LOGGER.debug("Invalid device response from %s: %s", sender_ip, response)
                    continue

                result = response["result"]

                # Build device info dict — use the target port we sent to,
                # not the response sender port, for reliable port recording.
                device = _build_device_info(
                    result,
                    result.get("ip", host),
                    port,
                )

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
