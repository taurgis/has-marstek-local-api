#!/usr/bin/env python3
"""Log Marstek restarts from the network side, without the regular Open API polls.

On Ethernet a unit that reboots drops off the LAN for the 30-60 s its boot
takes, and its Ethernet chip (CH395, DHCP enabled in Control firmware) asks
for an address again. Neither needs the Open API:

* ``ping`` once a second and print every gap, with when it started and how long
  it lasted. Wired, a gap of 20 s or more is a restart, a cable, or the switch.
* With ``--dhcp`` (needs tcpdump and root), also print every DHCP request the
  unit's MAC broadcasts. A DISCOVER or REQUEST right after a gap is the boot.
* With ``--getdata``, also print the reply to ``Marstek.GetData``, a read the
  firmware accepts but the Open API document does not list. It is sent once at
  start, after every gap, and every ``--getdata-every`` seconds. Its
  ``tol_charge``/``tol_discharge`` totals falling across a gap is the same
  "energy totals restored" trace the integration reads from ``ES.GetStatus``.

    python3 tools/watch_restarts_lan.py 192.168.1.50
    python3 tools/watch_restarts_lan.py 192.168.1.50 --getdata
    sudo python3 tools/watch_restarts_lan.py 192.168.1.50 --dhcp --mac aa:bb:cc:dd:ee:ff

Stop with Ctrl+C. Run it on any machine on the same LAN. Without ``--getdata``
it sends nothing to the Open API. ``--getdata`` binds the Open API port (the
unit replies to it), so run that on a machine other than Home Assistant's.
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
import subprocess
import sys
import threading
import time


def _stamp(moment: float) -> str:
    """Return a local wall-clock time for a log line."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))


def _ping_once(ip: str) -> bool:
    """Return whether one ICMP echo came back within a second."""
    if platform.system() == "Windows":
        command = ["ping", "-n", "1", "-w", "1000", ip]
    else:
        command = ["ping", "-c", "1", "-W", "1", ip]
    result = subprocess.run(command, capture_output=True, check=False)
    return result.returncode == 0


def print_getdata(ip: str, port: int) -> None:
    """Send ``Marstek.GetData`` (up to three tries) and print what came back."""
    request = json.dumps({"id": 1, "method": "Marstek.GetData", "params": {"id": 0}}).encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.settimeout(1.0)
        for _attempt in range(3):
            sock.sendto(request, (ip, port))
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                    reply = json.loads(data)
                except (TimeoutError, ValueError):
                    continue
                if addr[0] != ip or not isinstance(reply, dict):
                    continue
                if "result" in reply or "error" in reply:
                    body = reply.get("result", reply.get("error"))
                    print(f"{_stamp(time.time())} GetData {json.dumps(body)}", flush=True)
                    return
    print(f"{_stamp(time.time())} GetData: no reply", flush=True)


def watch_ping(ip: str, min_gap: float, getdata: tuple[int, float] | None) -> None:
    """Print each stretch the unit did not answer ping.

    ``getdata`` is (Open API port, seconds between reads), or None for no reads.
    """
    print(f"{_stamp(time.time())} watching {ip} (gaps of {min_gap:.0f} s or more)", flush=True)
    down_since: float | None = None
    next_read = time.time() if getdata else float("inf")
    while True:
        started = time.time()
        if _ping_once(ip):
            if down_since is not None:
                gap = started - down_since
                if gap >= min_gap:
                    print(f"{_stamp(down_since)} unreachable for {gap:.0f} s", flush=True)
                    # The Open API starts a little after the network does.
                    next_read = min(next_read, started + 15.0)
                down_since = None
            if getdata and started >= next_read:
                print_getdata(ip, getdata[0])
                next_read = time.time() + getdata[1]
        elif down_since is None:
            down_since = started
        time.sleep(max(0.0, 1.0 - (time.time() - started)))


def watch_dhcp(mac: str, interface: str | None) -> None:
    """Print every DHCP packet the unit's MAC sends, via tcpdump."""
    command = ["tcpdump", "-l", "-n", "-v"]
    if interface:
        command += ["-i", interface]
    command += ["ether", "src", mac, "and", "udp", "port", "67"]
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            if "DHCP-Message" in line:
                kind = line.split("DHCP-Message", 1)[1].strip(" :()\n")
                print(f"{_stamp(time.time())} DHCP {kind} from {mac}", flush=True)


def main() -> None:
    """Parse arguments and watch until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ip", help="The battery's LAN address")
    parser.add_argument("--min-gap", type=float, default=5.0, help="Shortest gap to print, seconds")
    parser.add_argument("--dhcp", action="store_true", help="Also watch DHCP (tcpdump, root)")
    parser.add_argument("--mac", help="The battery's Ethernet MAC, from your router's lease list")
    parser.add_argument("--interface", help="Interface tcpdump listens on")
    parser.add_argument("--getdata", action="store_true", help="Also print Marstek.GetData")
    parser.add_argument("--port", type=int, default=30000, help="Open API port (default 30000)")
    parser.add_argument(
        "--getdata-every", type=float, default=300.0, help="Seconds between GetData reads"
    )
    args = parser.parse_args()
    if args.dhcp:
        if not args.mac:
            parser.error("--dhcp needs --mac")
        threading.Thread(target=watch_dhcp, args=(args.mac, args.interface), daemon=True).start()
    try:
        watch_ping(args.ip, args.min_gap, (args.port, args.getdata_every) if args.getdata else None)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
