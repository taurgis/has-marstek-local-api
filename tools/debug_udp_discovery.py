#!/usr/bin/env python3
"""Standalone UDP discovery debug script for Marstek devices.

Tests UDP broadcast discovery without requiring the integration or pymarstek library.
Helps diagnose issues like echoed requests, missing responses, etc.

Usage:
    python3 tools/debug_udp_discovery.py
    python3 tools/debug_udp_discovery.py --timeout 15
    python3 tools/debug_udp_discovery.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import socket
import sys
from datetime import datetime
from typing import Any

# Configuration
DEFAULT_PORT = 30000
DEFAULT_TIMEOUT = 10.0


def get_broadcast_addresses() -> list[str]:
    """Get broadcast addresses for all network interfaces."""
    addresses: set[str] = {"255.255.255.255"}

    try:
        import psutil  # type: ignore[import]

        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    if getattr(addr, "broadcast", None):
                        addresses.add(addr.broadcast)
                    elif getattr(addr, "netmask", None):
                        try:
                            network = ipaddress.IPv4Network(
                                f"{addr.address}/{addr.netmask}", strict=False
                            )
                            addresses.add(str(network.broadcast_address))
                        except (ValueError, OSError):
                            continue

        # Remove local IPs from broadcast addresses
        local_ips = {
            addr.address
            for addrs in psutil.net_if_addrs().values()
            for addr in addrs
            if addr.family == socket.AF_INET
        }
        addresses -= local_ips

    except ImportError:
        print("⚠️  psutil not available, using only 255.255.255.255")
    except OSError as err:
        print(f"⚠️  Failed to get network interfaces: {err}")

    return list(addresses)


def is_echo_response(sent_request: dict, response: dict) -> bool:
    """Check if a response is just an echo of our request."""
    # Echo detection: response has 'method' and 'params' (request format)
    # instead of 'result' or 'error' (response format)
    # A valid device response should have 'result', not 'method'+'params'
    # A request echo carries the same method we sent, not a result.
    return (
        "method" in response
        and "params" in response
        and "result" not in response
        and response.get("method") == sent_request.get("method")
    )


def is_valid_device_response(response: dict) -> bool:
    """Check if response contains valid device info."""
    if "result" not in response:
        return False
    result = response["result"]
    if not isinstance(result, dict):
        return False
    # Valid device response should have at least one of these
    return any(key in result for key in ["device", "ip", "ble_mac", "wifi_mac"])


async def discover_devices_async(
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Discover Marstek devices via UDP broadcast."""
    print("\n" + "=" * 70)
    print("MARSTEK UDP DISCOVERY DEBUG")
    print("=" * 70)
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Port: {DEFAULT_PORT}")
    print(f"Timeout: {timeout}s")

    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)

    try:
        sock.bind(("0.0.0.0", DEFAULT_PORT))
        print(f"✅ Socket bound to 0.0.0.0:{DEFAULT_PORT}")
    except OSError as err:
        print(f"❌ Failed to bind socket: {err}")
        print("   Try: sudo lsof -i :30000  # to see what's using the port")
        return []

    loop = asyncio.get_running_loop()

    # Build discovery request
    # Use ID 0 - this is what the device expects and echoes back
    request_id = 0
    request = {
        "id": request_id,
        "method": "Marstek.GetDevice",
        "params": {"ble_mac": "0"},
    }
    message = json.dumps(request).encode("utf-8")

    # Get broadcast addresses
    broadcast_addrs = get_broadcast_addresses()
    print(f"\n📡 Broadcast addresses: {broadcast_addrs}")

    # Send broadcasts
    print("\n🔄 Sending discovery request:")
    print(f"   {json.dumps(request)}")

    for addr in broadcast_addrs:
        try:
            sock.sendto(message, (addr, DEFAULT_PORT))
            print(f"   ✅ Sent to {addr}:{DEFAULT_PORT}")
        except OSError as err:
            print(f"   ❌ Failed to send to {addr}: {err}")

    # Collect responses
    print(f"\n⏳ Waiting for responses (timeout: {timeout}s)...")

    responses: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    seen_ips: set[str] = set()
    echoes_filtered = 0
    invalid_responses = 0
    start_time = loop.time()

    while (loop.time() - start_time) < timeout:
        try:
            data, addr = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 4096),
                timeout=0.5,
            )

            response_text = data.decode("utf-8")
            sender_ip, sender_port = addr

            try:
                response = json.loads(response_text)
            except json.JSONDecodeError:
                print(f"   ⚠️  Invalid JSON from {sender_ip}:{sender_port}: {response_text[:100]}")
                invalid_responses += 1
                continue

            response_id = response.get("id")

            # Check if this is an echo of our request
            if is_echo_response(request, response):
                echoes_filtered += 1
                if verbose:
                    print(f"   🔄 ECHO filtered from {sender_ip}:{sender_port} (id={response_id})")
                continue

            # Check for valid response ID
            # For broadcast discovery, some devices may respond with ID 0 regardless of request ID
            # Accept both matching ID and ID 0 for discovery
            if response_id != request_id and response_id != 0:
                if verbose:
                    print(
                        f"   ⚠️  Unexpected response ID {response_id} "
                        f"from {sender_ip}:{sender_port}"
                    )
                continue

            print(f"\n   📥 Response from {sender_ip}:{sender_port}:")
            if verbose:
                print(f"      Raw: {json.dumps(response, indent=2)}")

            responses.append({"response": response, "addr": addr})

            # Check for valid device info
            if is_valid_device_response(response):
                result = response["result"]
                device_ip = result.get("ip", sender_ip)

                if device_ip in seen_ips:
                    print(f"      ⚠️  Duplicate device at {device_ip}, skipping")
                    continue

                seen_ips.add(device_ip)

                device = {
                    "device_type": result.get("device", "Unknown"),
                    "ip": device_ip,
                    "version": result.get("ver", 0),
                    "wifi_name": result.get("wifi_name", ""),
                    "wifi_mac": result.get("wifi_mac", ""),
                    "ble_mac": result.get("ble_mac", ""),
                }
                devices.append(device)

                print(f"      ✅ VALID DEVICE: {device['device_type']} at {device['ip']}")
                print(f"         Version: {device['version']}")
                print(f"         BLE MAC: {device['ble_mac']}")
                print(f"         WiFi MAC: {device['wifi_mac']}")
            else:
                print("      ❌ Invalid/incomplete response - missing 'result' or device fields")
                if "error" in response:
                    print(f"         Error: {response['error']}")
                if verbose:
                    print(f"         Response keys: {list(response.keys())}")
                invalid_responses += 1

        except TimeoutError:
            # No response in this interval, continue waiting
            continue
        except OSError as err:
            print(f"   ❌ Socket error: {err}")
            break

    sock.close()

    # Summary
    print("\n" + "-" * 70)
    print("DISCOVERY SUMMARY")
    print("-" * 70)
    print(f"Total responses received: {len(responses)}")
    print(f"Echoes filtered: {echoes_filtered}")
    print(f"Invalid responses: {invalid_responses}")
    print(f"Valid devices found: {len(devices)}")

    if devices:
        print("\n✅ DEVICES FOUND:")
        for i, dev in enumerate(devices, 1):
            print(f"   {i}. {dev['device_type']} - {dev['ip']}")
            print(f"      BLE MAC: {dev['ble_mac']}, WiFi MAC: {dev['wifi_mac']}")
    else:
        print("\n❌ NO DEVICES FOUND")
        print("\nTROUBLESHOOTING:")
        print("   1. Ensure your Marstek device is powered on")
        print("   2. Verify OPEN API is enabled in the Marstek app")
        print("   3. Check device and computer are on the same network/VLAN")
        print("   4. Try: nc -u -l 30000  # to verify UDP traffic reaches your machine")

        if echoes_filtered > 0:
            print(f"\n⚠️  {echoes_filtered} echo(es) were filtered!")
            print("   This means your broadcast is being received but no device responded.")
            print("   This often indicates a network configuration issue.")

    print("=" * 70 + "\n")

    return devices


def discover_devices_sync(timeout: float = DEFAULT_TIMEOUT, verbose: bool = False) -> list[dict]:
    """Synchronous wrapper for discovery."""
    return asyncio.run(discover_devices_async(timeout=timeout, verbose=verbose))


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Debug Marstek UDP device discovery"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Discovery timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show verbose output including raw responses",
    )

    args = parser.parse_args()

    devices = discover_devices_sync(timeout=args.timeout, verbose=args.verbose)

    # Exit code based on result
    sys.exit(0 if devices else 1)


if __name__ == "__main__":
    main()
