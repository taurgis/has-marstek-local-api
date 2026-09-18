#!/usr/bin/env python3
"""Query a specific Marstek device directly."""

import argparse
import asyncio
import json
import socket


async def query_device(host: str, port: int = 30000, timeout: float = 5.0):
    """Send GetDevice request directly to a device IP."""
    print(f"Querying {host}:{port}...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)
    # Bind to the device port; Marstek firmware replies to that port.
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as err:
        print(f"Failed to bind UDP port {port}: {err}")
        sock.close()
        return None

    request = {"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}
    message = json.dumps(request).encode()

    loop = asyncio.get_running_loop()
    sock.sendto(message, (host, port))
    print(f"Sent: {json.dumps(request)}")

    start = loop.time()
    while (loop.time() - start) < timeout:
        try:
            data, addr = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 4096), timeout=0.5
            )
            response = json.loads(data.decode())
            print(f"\nResponse from {addr[0]}:{addr[1]}:")
            print(json.dumps(response, indent=2))

            # Check if this is a valid response (not an echo)
            if "result" in response:
                print("\n✅ Valid device response!")
                sock.close()
                return response
            elif "method" in response and "params" in response:
                print("   (echo of our request, continuing...)")

        except asyncio.TimeoutError:
            continue

    sock.close()
    print("\n❌ No valid response received")
    return None


def main():
    parser = argparse.ArgumentParser(description="Query a Marstek device directly")
    parser.add_argument("host", help="Device IP address")
    parser.add_argument("--port", type=int, default=30000, help="UDP port (default: 30000)")
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout in seconds")
    args = parser.parse_args()

    asyncio.run(query_device(args.host, args.port, args.timeout))


if __name__ == "__main__":
    main()
