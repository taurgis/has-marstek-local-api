"""Exit 0 once the emulated firmware answers ``Marstek.GetDevice`` on its Local API port.

Used as the Docker healthcheck. It sends from an ephemeral port: the emulated
modem hands the firmware the real source address and the firmware replies there.
The firmware drops requests while it is busy with a CT reply, so one probe sends
a few times before giving up.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time


def probe(host: str, port: int, timeout: float, attempts: int) -> dict[str, object] | None:
    """Return the ``GetDevice`` result, or None when nothing valid came back."""
    request = {"id": 4242, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _ in range(attempts):
            sock.sendto(json.dumps(request).encode(), (host, port))
            deadline = time.monotonic() + timeout
            while (remaining := deadline - time.monotonic()) > 0:
                sock.settimeout(remaining)
                try:
                    data, _addr = sock.recvfrom(4096)
                except TimeoutError:
                    break
                try:
                    reply = json.loads(data)
                except ValueError:
                    continue
                result = reply.get("result") if isinstance(reply, dict) else None
                if reply.get("id") == request["id"] and isinstance(result, dict):
                    return result
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30000)
    parser.add_argument("--timeout", type=float, default=6.0, help="Seconds per attempt")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    result = probe(args.host, args.port, args.timeout, args.attempts)
    if result is None:
        print("no Marstek.GetDevice reply", file=sys.stderr)
        return 1
    print(f"{result.get('device')} v{result.get('ver')} {result.get('ble_mac')} {result.get('ip')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
