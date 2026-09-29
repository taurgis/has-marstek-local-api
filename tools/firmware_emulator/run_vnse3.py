"""Boot the vendor VNSE3-0 (Venus E 3.0) Control firmware in Renode on this host.

Renders the Renode platform/script templates, starts Renode headless, starts the
FC41D modem emulator (``fc41d.py``) on the firmware's Wi-Fi UART and runs the
machine. The firmware's Local API then answers on real UDP port 30000 of this
host, so Home Assistant (or ``tools/query_device.py``) talks to it unmodified.
``can_peers.py`` plays the BMS and inverter on the CAN bus and serves the grid
meter reading that AstraMeter turns into CT003 replies (see README.md).

The firmware blob is not in the repository; fetch it with
``tools/firmware/fetch_firmware.py`` (it lands in ``tools/firmware/blobs/``).

A fresh EEPROM image gets its defaults from the firmware on the first boot,
with the Local API disabled. The EEPROM model then forces it on, and this
script reboots once so the firmware reads the enabled setting.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_BLOB = REPO / "tools/firmware/blobs/VNSEE3-0_app_0150_0804_151249.bin"
UART_PORT = 3456

RESC = """\
mach create "vnse3"
include @{renode_dir}/I2CEeprom.cs
include @{renode_dir}/CanBridge.cs
machine LoadPlatformDescription @{repl}
sysbus LoadBinary @{blob} 0x08004800
cpu VectorTableOffset 0x08004800
sysbus.cpu SP 0x2001f5e0
sysbus.cpu PC 0x08004a71
logLevel 2
{usarts}
emulation CreateServerSocketTerminal {uart_port} "wifi" false
connector Connect sysbus.wifi_uart wifi
emulation CreateCANHub "canhub"
connector Connect sysbus.can1 canhub
connector Connect sysbus.canbridge canhub
"""


class Monitor:
    """Minimal client for the Renode telnet monitor."""

    def __init__(self, port: int) -> None:
        deadline = time.monotonic() + 60
        while True:
            try:
                self._sock = socket.create_connection(("127.0.0.1", port))
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self._sock.settimeout(0.5)
        self._drain(1.0)

    def _drain(self, seconds: float) -> str:
        buf = b""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = self._sock.recv(65536)
            except TimeoutError:
                continue
            if not data:
                break
            buf += data
        return re.sub(rb"\x1b\[[0-9;]*[a-zA-Z]", b"", buf).decode("latin1")

    def close(self) -> None:
        """Release the monitor; Renode serves one client at a time."""
        self._sock.close()

    def run(self, command: str, wait: float = 1.5) -> str:
        self._sock.sendall(command.encode() + b"\n")
        return self._drain(wait)


def _render(args: argparse.Namespace, work: Path) -> Path:
    renode_dir = HERE / "renode"
    repl = (renode_dir / "vnse3.repl.in").read_text()
    for key, value in {
        "@RENODE_DIR@": str(renode_dir),
        "@EEPROM@": str(args.eeprom),
        "@LOCAL_API_PORT@": str(args.local_api_port),
    }.items():
        repl = repl.replace(key, value)
    (work / "vnse3.repl").write_text(repl)
    usarts = "\n".join(f"usart{n} CreateFileBackend @{work}/usart{n}.txt true" for n in range(1, 6))
    resc = RESC.format(
        renode_dir=renode_dir,
        repl=work / "vnse3.repl",
        blob=args.blob,
        usarts=usarts,
        uart_port=UART_PORT,
    )
    (work / "vnse3.resc").write_text(resc)
    return work / "vnse3.resc"


def _eeprom_initialised(path: Path) -> bool:
    try:
        return path.read_bytes()[:1] not in (b"", b"\xff")
    except OSError:
        return False


def _boot(mon: Monitor, resc: Path, fc41d_cmd: list[str], log: Path) -> subprocess.Popen[bytes]:
    mon.run("mach clear")
    mon.run("Clear")
    mon.run(f"include @{resc}", wait=15)
    with log.open("ab") as out:
        modem = subprocess.Popen(fc41d_cmd, stdout=out, stderr=subprocess.STDOUT)
    time.sleep(1)
    mon.run("start")
    return modem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--renode", required=True, help="Path to the renode launcher")
    parser.add_argument("--blob", type=Path, default=DEFAULT_BLOB)
    parser.add_argument("--ip", required=True, help="IP the emulated device reports")
    parser.add_argument("--gateway", default="172.28.0.1")
    parser.add_argument("--local-api-port", type=int, default=30000)
    parser.add_argument("--work", type=Path, default=Path("/tmp/vnse3"))
    parser.add_argument("--eeprom", type=Path, help="EEPROM image (default: WORK/eeprom.bin)")
    parser.add_argument("--monitor-port", type=int, default=41234)
    parser.add_argument("--soc", type=float, default=50.0, help="Initial battery SoC")
    parser.add_argument("--house-load", default="300,150,150", help="Per-phase load in W")
    args = parser.parse_args()

    if not args.blob.is_file():
        parser.error(f"firmware blob not found: {args.blob} (run tools/firmware/fetch_firmware.py)")
    args.work.mkdir(parents=True, exist_ok=True)
    args.eeprom = (args.eeprom or args.work / "eeprom.bin").resolve()
    resc = _render(args, args.work)
    fc41d_cmd = [
        sys.executable, str(HERE / "fc41d.py"), "--uart-port", str(UART_PORT),
        "--ip", args.ip, "--gateway", args.gateway, "-v",
    ]  # fmt: skip
    modem_log = args.work / "fc41d.log"

    renode = subprocess.Popen(
        [args.renode, "--disable-gui", "-P", str(args.monitor_port)],
        stdout=(args.work / "renode.log").open("ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    modem: subprocess.Popen[bytes] | None = None
    peers: subprocess.Popen[bytes] | None = None
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        mon = Monitor(args.monitor_port)
        fresh = not _eeprom_initialised(args.eeprom)
        modem = _boot(mon, resc, fc41d_cmd, modem_log)
        if fresh:
            print("Fresh EEPROM: waiting for the firmware defaults, then rebooting once")
            deadline = time.monotonic() + 180
            while not _eeprom_initialised(args.eeprom) and time.monotonic() < deadline:
                time.sleep(2)
            time.sleep(5)
            modem.terminate()
            modem.wait()
            modem = _boot(mon, resc, fc41d_cmd, modem_log)
        with (args.work / "can_peers.log").open("ab") as out:
            peers = subprocess.Popen(
                [sys.executable, str(HERE / "can_peers.py"), "--soc", str(args.soc),
                 "--house-load", args.house_load],
                stdout=out,
                stderr=subprocess.STDOUT,
            )  # fmt: skip
        print(f"Running. Local API on UDP {args.local_api_port}; modem log {modem_log}")
        mon.close()
        renode.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for child in (peers, modem):
            if child is not None:
                child.terminate()
        if renode.poll() is None:
            os.killpg(renode.pid, signal.SIGTERM)
    return 0


if __name__ == "__main__":
    sys.exit(main())
