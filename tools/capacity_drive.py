"""Cycle a device in Passive mode and feed every poll to the capacity estimator.

Charges until the device reports 100 % (and stays there ``--hold-full``
seconds), discharges to ``--low``, and repeats. Each ``ES.GetStatus`` goes
through the integration's own parser and ``apply_capacity_estimate``, so a
printed line is what Home Assistant would hold after that poll. Pair it with
``tools/firmware_emulator`` and ``can_peers.py --bms-*`` to see a drifting
BMS through the vendor firmware, or with ``tools/mock_device --soc-jump-*``.

    python3 tools/capacity_drive.py 172.17.0.2 --out run.jsonl
    python3 tools/capacity_drive.py 127.0.0.1 --port 30011 --bind 0   # mock on loopback
    python3 tools/capacity_drive.py --replay run.jsonl --seed-wh 1536

``--seed-wh`` starts the estimator with a published capacity, as on an
install that has run for a few days; without one only steps the watts
cannot explain count as corrections. Run it from the repository root in the
Home Assistant test venv.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.marstek.capacity_estimator import (
    CAPACITY_ATTR_KEY,
    SOC_CORRECTED_KEY,
    UsableCapacityEstimator,
    apply_capacity_estimate,
)
from custom_components.marstek.pymarstek.data_parser import (
    parse_es_status_response,
)

# The emulated Passive countdown can end within minutes; re-send it this often.
REASSERT_S = 60.0
REPLY_TIMEOUT_S = 6.0
# Control firmware drops requests for a moment after it answers.
QUIET_S = 2.0


class Device:
    """One Open API device over UDP, a request at a time."""

    def __init__(self, ip: str, port: int, bind: int) -> None:
        self.ip, self.port, self._id = ip, port, 100
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", bind))

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        self._id += 1
        request = {"id": self._id, "method": method, "params": params}
        self.sock.sendto(json.dumps(request, separators=(",", ":")).encode(), (self.ip, self.port))
        deadline = time.monotonic() + REPLY_TIMEOUT_S
        while (left := deadline - time.monotonic()) > 0:
            self.sock.settimeout(left)
            try:
                data, _ = self.sock.recvfrom(65536)
            except TimeoutError:
                return None
            try:
                reply = json.loads(data)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("id") == self._id:
                return reply
        return None

    def passive(self, power: int) -> bool:
        config = {"mode": "Passive", "passive_cfg": {"power": power, "cd_time": 7200}}
        for _ in range(4):
            reply = self.call("ES.SetMode", {"id": 0, "config": config})
            if reply and reply.get("result", {}).get("set_result"):
                return True
            time.sleep(QUIET_S + 1)
        return False


def seeded_estimator(seed_wh: float | None, before: float) -> UsableCapacityEstimator:
    """Return an estimator, already publishing ``seed_wh`` when given."""
    if seed_wh is None:
        return UsableCapacityEstimator()
    runs = [("charge", 25), ("discharge", -25)] * 3
    windows = [
        {
            "capacity_wh": seed_wh,
            "direction": d,
            "soc_delta": s,
            "closed_at": before - 3600 * (n + 1),
        }
        for n, (d, s) in enumerate(runs)
    ]
    return UsableCapacityEstimator.from_dict({"windows": windows})


def record(estimator: UsableCapacityEstimator, row: dict[str, Any]) -> dict[str, Any]:
    """Apply one poll the way the coordinator does and return the outcome."""
    status = {
        "battery_soc": row["soc"],
        "battery_power": row["power"],
        "bat_cap": row["bat_cap"],
        "device_mode": "passive",
    }
    apply_capacity_estimate(estimator, status, row["t"])
    attrs = status[CAPACITY_ATTR_KEY]
    corrections = attrs.get("soc_corrections") or []
    return {
        **row,
        "corrected": status[SOC_CORRECTED_KEY],
        "estimate": estimator.estimate_wh,
        "windows": attrs.get("windows_accepted"),
        "bound": attrs.get("soc_drift_bound_pct"),
        "last_correction": corrections[-1] if corrections else None,
    }


def replay(path: Path, seed_wh: float | None) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return
    estimator = seeded_estimator(seed_wh, rows[0]["t"])
    for row in rows:
        out = record(estimator, row)
        if out["corrected"]:
            print(json.dumps(out), flush=True)
    print(json.dumps({"final": estimator.attributes()}, default=str), flush=True)


def drive(args: argparse.Namespace) -> None:
    device = Device(args.ip, args.port, args.port if args.bind is None else args.bind)
    estimator = seeded_estimator(args.seed_wh, time.time())
    with contextlib.ExitStack() as stack:
        out = stack.enter_context(Path(args.out).open("a", encoding="utf-8")) if args.out else None
        _cycle(args, device, estimator, out)
    print(json.dumps({"final": estimator.attributes()}, default=str), flush=True)


def _cycle(
    args: argparse.Namespace,
    device: Device,
    estimator: UsableCapacityEstimator,
    out: Any,
) -> None:
    """Charge to full and discharge to ``--low`` for ``--cycles`` rounds."""
    phase, cycles, full_since = "charge", 0, None

    def command() -> None:
        if not device.passive(-args.power if phase == "charge" else args.power):
            print("ES.SetMode unanswered", flush=True)

    command()
    asserted = time.monotonic()
    while cycles < args.cycles:
        time.sleep(args.poll)
        if time.monotonic() - asserted > REASSERT_S:
            time.sleep(QUIET_S)
            command()
            asserted = time.monotonic()
            time.sleep(QUIET_S)
        reply = device.call("ES.GetStatus", {"id": 0})
        if not reply or "result" not in reply:
            print("no reply", flush=True)
            continue
        status = parse_es_status_response(reply)
        row = {"t": round(time.time(), 1), "phase": phase, "soc": status.get("battery_soc")}
        row |= {"power": status.get("battery_power"), "bat_cap": status.get("bat_cap")}
        if out:
            out.write(json.dumps(row) + "\n")
            out.flush()
        print(json.dumps(record(estimator, row)), flush=True)
        soc = row["soc"]
        if not isinstance(soc, (int, float)):
            continue
        now = time.time()
        if phase == "charge" and soc >= 100:
            full_since = full_since or now
            if now - full_since >= args.hold_full:
                phase, full_since = "discharge", None
                command()
                asserted = time.monotonic()
        elif phase == "discharge" and soc <= args.low:
            phase, cycles = "charge", cycles + 1
            command()
            asserted = time.monotonic()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ip", nargs="?", help="Device address (omit with --replay)")
    parser.add_argument("--port", type=int, default=30000, help="Open API port")
    parser.add_argument("--bind", type=int, help="Local UDP port (default: --port; 0 for loopback)")
    parser.add_argument("--cycles", type=int, default=1, help="Full charge-discharge cycles")
    parser.add_argument("--poll", type=float, default=10.0, help="Seconds between polls")
    parser.add_argument("--power", type=int, default=2500, help="Passive W each way")
    parser.add_argument("--low", type=float, default=20.0, help="Discharge down to this SoC")
    parser.add_argument("--hold-full", type=float, default=120.0, help="Seconds to rest at 100%%")
    parser.add_argument("--seed-wh", type=float, help="Start with this published capacity")
    parser.add_argument("--out", help="Append each poll as JSON lines (input for --replay)")
    parser.add_argument("--replay", type=Path, help="Replay a --out file instead of a device")
    args = parser.parse_args()
    if args.replay:
        replay(args.replay, args.seed_wh)
    elif args.ip:
        drive(args)
    else:
        parser.error("give a device IP or --replay FILE")


if __name__ == "__main__":
    main()
