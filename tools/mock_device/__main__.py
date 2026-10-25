"""Entry point for mock Marstek device."""

from __future__ import annotations

import argparse

from .const import DEFAULT_CONFIG, DEFAULT_HOUSE_PV_WP, DEFAULT_PHASE_COUNT, DEFAULT_UDP_PORT
from .device import DEFAULT_STATUS_INTERVAL, MockMarstekDevice
from .utils import DEFAULT_STATE_DIR


def firmware_version(value: str) -> int:
    """Parse a non-negative integer firmware version for argparse."""
    try:
        version = int(value, 10)
    except ValueError as err:
        raise argparse.ArgumentTypeError("firmware must be an integer") from err
    if version < 0:
        raise argparse.ArgumentTypeError("firmware must be non-negative")
    return version


def _add_experiment_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the capacity-experiment flags."""
    parser.add_argument(
        "--capacity-wh",
        type=int,
        default=None,
        help=(
            "True DC capacity used for state-of-charge physics. "
            "The Open API nameplate (bat_cap / rated_capacity) stays at the "
            "family rating, so a faded pack is not visible on those fields."
        ),
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help=(
            "Accelerate pack physics and the reported inverter and PV watts "
            "by this factor (default: 1). Wall-clock integration of reported "
            "power still matches state of charge."
        ),
    )
    parser.add_argument(
        "--charge-efficiency",
        type=float,
        default=None,
        help="One-way AC-to-pack efficiency (default: 0.93)",
    )
    parser.add_argument(
        "--discharge-efficiency",
        type=float,
        default=None,
        help="One-way pack-to-AC efficiency (default: 0.93)",
    )
    parser.add_argument(
        "--pack-self-consumption-w",
        type=float,
        default=0.0,
        help="DC watts the pack loses without reporting them on the AC port",
    )
    parser.add_argument(
        "--soc-jump-at",
        type=float,
        default=None,
        help="While charging, add --soc-jump-by once state of charge crosses this",
    )
    parser.add_argument(
        "--soc-jump-by",
        type=float,
        default=0.0,
        help="One-shot state-of-charge jump in percentage points (default: 0)",
    )
    parser.add_argument(
        "--power-fluctuation",
        type=float,
        default=None,
        help="Setpoint tracking error in percent (default: the simulator's 1.5)",
    )
    parser.add_argument(
        "--standby-w",
        type=int,
        default=None,
        help="Watts the unit draws from the mains for itself (default: the family's)",
    )
    parser.add_argument(
        "--plug-http-port",
        type=int,
        default=None,
        help=(
            "Serve a smart plug on the battery's AC cord over HTTP on this port: "
            "a HomeWizard Energy Socket (API v1) and a Shelly-style /rpc endpoint"
        ),
    )
    parser.add_argument(
        "--fixed-pv",
        action="store_true",
        help="Hold --pv-channels at the given watts instead of following the sun",
    )
    parser.add_argument(
        "--soc-cycle",
        action="store_true",
        help="Hold Passive mode and cycle between 30%% and 92%% SOC at rated power",
    )


def _experiment_config(args: argparse.Namespace) -> dict[str, object]:
    """Return capacity-experiment keys, or exit when a value is unusable."""
    config: dict[str, object] = {}
    if args.capacity_wh is not None and args.capacity_wh <= 0:
        raise SystemExit("--capacity-wh must be a positive number of watt-hours")
    if args.time_scale <= 0:
        raise SystemExit("--time-scale must be positive")
    if args.capacity_wh is not None:
        config["capacity_wh"] = args.capacity_wh
    if args.time_scale != 1.0:
        config["time_scale"] = args.time_scale
    if args.soc_cycle:
        config["soc_cycle"] = True
    if args.charge_efficiency is not None:
        config["charge_efficiency"] = args.charge_efficiency
    if args.discharge_efficiency is not None:
        config["discharge_efficiency"] = args.discharge_efficiency
    if args.pack_self_consumption_w:
        config["pack_self_consumption_w"] = args.pack_self_consumption_w
    if args.soc_jump_at is not None:
        config["soc_jump_at"] = args.soc_jump_at
        config["soc_jump_by"] = args.soc_jump_by
    if args.power_fluctuation is not None:
        config["power_fluctuation_pct"] = args.power_fluctuation
    if args.fixed_pv:
        config["fixed_pv"] = True
    if args.standby_w is not None:
        if args.standby_w < 0:
            raise SystemExit("--standby-w must be non-negative")
        config["standby_w"] = args.standby_w
    if args.plug_http_port is not None:
        if not 0 < args.plug_http_port < 65536:
            raise SystemExit("--plug-http-port must be a TCP port")
        config["plug_http_port"] = args.plug_http_port
    return config


def main() -> None:
    """Run mock Marstek device."""
    parser = argparse.ArgumentParser(
        description="Mock Marstek device for testing with realistic battery simulation"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help=f"UDP port (default: {DEFAULT_UDP_PORT})",
    )
    parser.add_argument("--ip", type=str, help="Override reported IP address")
    parser.add_argument(
        "--stale-ip",
        type=str,
        default=None,
        help=(
            "IPv4 to put in Marstek.GetDevice ip while Wifi.GetStatus sta_ip "
            "stays the live address (issue #108)"
        ),
    )
    parser.add_argument("--device", type=str, default="VenusE 3.0", help="Device type")
    parser.add_argument(
        "--ver",
        type=firmware_version,
        default=DEFAULT_CONFIG["ver"],
        help=f"Firmware version (default: {DEFAULT_CONFIG['ver']})",
    )
    parser.add_argument("--ble-mac", type=str, default="009b08a5aa39", help="BLE MAC address")
    parser.add_argument("--wifi-mac", type=str, default="7483c2315cf8", help="WiFi MAC address")
    parser.add_argument(
        "--soc",
        type=int,
        default=50,
        help="Initial battery SOC percentage (default: 50)",
    )
    _add_experiment_arguments(parser)
    parser.add_argument(
        "--pv-channels",
        type=str,
        help=(
            "Optional PV channel values for VenusD in the format "
            "'power:voltage:current, ...' (up to 4 channels). "
            "Example: '300:40:7.5,250:38:6.6,200:36:5.5,0:0:0'"
        ),
    )
    parser.add_argument(
        "--house-pv-wp",
        type=int,
        default=None,
        help=(
            "Rooftop PV peak power in watts for the simulated home "
            f"(default: {DEFAULT_HOUSE_PV_WP}, or 0 when --pv-channels is given; "
            "0 disables rooftop solar)"
        ),
    )
    parser.add_argument(
        "--phases",
        type=int,
        choices=(1, 3),
        default=DEFAULT_PHASE_COUNT,
        help=(
            "Phases the home is supplied on. The Venus is single-phase either "
            f"way; on 3 it only offsets phase A (default: {DEFAULT_PHASE_COUNT})"
        ),
    )
    parser.add_argument(
        "--no-simulate",
        action="store_true",
        help="Disable dynamic simulation (static values only)",
    )
    parser.add_argument(
        "--state-dir",
        type=str,
        default=str(DEFAULT_STATE_DIR),
        help="Directory to store persisted mock device state",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset persisted state for this device",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not log a line per handled request (drops are still summarised)",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=DEFAULT_STATUS_INTERVAL,
        help=(
            "Seconds between simulator status lines "
            f"(default: {DEFAULT_STATUS_INTERVAL:g}; 0 disables them)"
        ),
    )
    args = parser.parse_args()

    config = {
        "device": args.device,
        "ver": args.ver,
        "ble_mac": args.ble_mac.replace(":", "").lower(),
        "wifi_mac": args.wifi_mac.replace(":", "").lower(),
    }

    config.update(_experiment_config(args))

    if args.stale_ip:
        config["stale_ip"] = args.stale_ip

    if args.pv_channels:
        channels: list[dict[str, float]] = []
        for idx, chunk in enumerate(args.pv_channels.split(","), start=1):
            parts = chunk.split(":")
            if len(parts) != 3:
                raise SystemExit(
                    "Invalid --pv-channels format. Expected 'power:voltage:current' per channel."
                )
            try:
                power = float(parts[0])
                voltage = float(parts[1])
                current = float(parts[2])
            except ValueError as exc:
                raise SystemExit("Invalid numeric values in --pv-channels.") from exc
            channels.append(
                {
                    "channel": idx,
                    "pv_power": power,
                    "pv_voltage": voltage,
                    "pv_current": current,
                }
            )
        config["pv_channels"] = channels

    device = MockMarstekDevice(
        port=args.port,
        device_config=config,
        ip_override=args.ip,
        initial_soc=args.soc,
        simulate=not args.no_simulate,
        state_dir=args.state_dir,
        reset_state=args.reset_state,
        verbose=not args.quiet,
        status_interval=args.status_interval,
        house_pv_wp=args.house_pv_wp,
        phases=args.phases,
    )
    device.start()


if __name__ == "__main__":
    main()
