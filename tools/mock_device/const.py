"""Constants for mock Marstek device."""

from __future__ import annotations

from dataclasses import dataclass

from custom_components.marstek.firmware_profile import DeviceFamily, resolve_firmware_profile

# Default mock device configuration
# Using clearly fake MAC addresses (locally administered range: 02:xx:xx:xx:xx:xx)
# These should NEVER match real device MACs
DEFAULT_CONFIG = {
    "device": "VenusE 3.0",
    "ver": 145,
    "ble_mac": "02deadbeef01",  # Mock BLE MAC - device 1
    "wifi_mac": "02cafebabe01",  # Mock WiFi MAC - device 1
    "wifi_name": "MockNetwork",
}

# Mock MAC address prefixes for multi-device testing
# Format: {prefix}{device_number:02d} e.g. 02deadbeef01, 02deadbeef02
MOCK_BLE_PREFIX = "02deadbeef"  # + 2-digit hex device number
MOCK_WIFI_PREFIX = "02cafebabe"  # + 2-digit hex device number

# Battery capacity in Wh (typical for Venus 3.0 is ~5120Wh)
BATTERY_CAPACITY_WH = 5120

# Mode constants
MODE_AUTO = "Auto"
MODE_AI = "AI"
MODE_MANUAL = "Manual"
MODE_PASSIVE = "Passive"
MODE_UPS = "UPS"

# Battery status labels (lowercase translation keys)
STATUS_CHARGING = "charging"
STATUS_DISCHARGING = "discharging"
STATUS_IDLE = "idle"

# Default simulation settings (2500W matches typical Venus 3.0 limits)
DEFAULT_MAX_CHARGE_POWER = 2500
DEFAULT_MAX_DISCHARGE_POWER = 2500
DEFAULT_POWER_FLUCTUATION_PCT = 1.5  # ±1.5% setpoint tracking error
DEFAULT_UPDATE_INTERVAL = 1.0  # seconds
DEFAULT_UDP_PORT = 30000

# SOC limits
SOC_MIN_DISCHARGE = 5  # Don't discharge below this
SOC_TAPER_DISCHARGE = 10  # Start tapering discharge below this
SOC_TAPER_CHARGE = 90  # Start tapering charge above this
SOC_RESERVE = 10  # Reserve for auto mode

# --- Inverter and pack physics -------------------------------------------
# One-way conversion efficiency. Marstek publishes ">88% battery-to-AC" for
# the Venus line; 0.93 each way gives an ~86% AC round trip, which is what
# independent measurements of a Venus E actually show.
CHARGE_EFFICIENCY = 0.93
DISCHARGE_EFFICIENCY = 0.93
# How fast the inverter may slew its AC setpoint. A real unit needs a second
# or two to cross its full range, which is why a kettle switching on shows up
# on the P1 meter before the battery covers it.
POWER_RAMP_W_PER_SECOND = 2000.0
# Auxiliary draw of the unit itself (fans, BMS, radios), seen by the P1 meter
# as just another house load because that is exactly what it is.
DEFAULT_STANDBY_POWER_W = 15

# --- Auto/AI regulation loop ---------------------------------------------
# The device reads its CT, not the true house load. A first-order lag plus
# quantisation is what a 1 Hz CT link looks like from the controller's side.
CT_MEASUREMENT_LAG_SECONDS = 1.5
CT_NOISE_W = 4
# Do not chase imbalances smaller than this; real units idle around zero
# instead of hunting.
AUTO_DEADBAND_W = 30
# Loop gain below 1.0 keeps the lagged measurement from causing overshoot.
AUTO_LOOP_GAIN = 0.8

# --- Thermal model --------------------------------------------------------
# The pack and its enclosure are treated as one lumped body. Heat capacity
# and heat-transfer coefficient scale with pack size: for a 5120 Wh unit
# that is ~45 kJ/K and ~15 W/K, so continuous full-power operation settles
# around 12 K above ambient with a ~50 minute time constant.
THERMAL_CAPACITY_J_PER_K_PER_WH = 8.8
THERMAL_CONDUCTANCE_W_PER_K_PER_WH = 0.003
# LFP charge/discharge windows the BMS enforces.
BAT_TEMP_MIN_CHARGE_C = 0.0
BAT_TEMP_MAX_C = 50.0
# Ambient around an indoor/garage install.
AMBIENT_MEAN_C = 18.0
AMBIENT_SWING_C = 5.0

# --- Home model -----------------------------------------------------------
# Rooftop array in watt-peak for the simulated dwelling. A Marstek battery
# next to no panels never sees an export surplus, so Auto mode could only
# ever discharge and the mock would sit at its reserve forever.
DEFAULT_HOUSE_PV_WP = 3500
# Most Venus installs are a plug on one phase of a single-phase supply.
DEFAULT_PHASE_COUNT = 1


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """Physical characteristics of a Marstek device family."""

    capacity_wh: int
    max_charge_power: int
    max_discharge_power: int
    standby_power: int = DEFAULT_STANDBY_POWER_W


# Rated pack capacity and rated AC power per family.
#
# Capacities: ``5120`` is the ``bat_cap`` a Venus E 3.0 firmware 150 LAN
# capture reports and ``2080`` is the ``rated_capacity`` from the Venus A
# firmware 147 capture in issue #11. The remaining families have no capture in
# this repository and use the capacity Marstek publishes for the model, so a
# mock never reports a Venus A-sized pack as a Venus E.
#
# Powers mirror the integration's own per-family ceilings
# (``custom_components/marstek/const.py::_FAMILY_POWER_LIMITS``) so the mock can
# never claim a capability the integration would refuse to command. The table
# is duplicated rather than imported because the mock runs in a container with
# no Home Assistant installed, and that module imports ``homeassistant.const``.
# ``tests/test_mock_device`` asserts the two stay in agreement.
# Each entry is ``(capacity_wh, rated_ac_power_w)``.
_FAMILY_SPECS: dict[DeviceFamily, tuple[int, int]] = {
    DeviceFamily.VENUS_A: (2080, 1500),
    DeviceFamily.VENUS_C: (2560, 2500),
    DeviceFamily.VENUS_D: (2560, 2200),
    DeviceFamily.VENUS_E: (5120, 2500),
    # The mini is rated 1.5 kVA even though the integration allows the family
    # the generic 2500 W ceiling; the mock reports what the hardware can do.
    DeviceFamily.VENUS_E_MINI: (2010, 1500),
}


def device_spec(device_type: str | None) -> DeviceSpec:
    """Return the physical spec for a device type string."""
    family = resolve_firmware_profile(device_type, None).family
    if family not in _FAMILY_SPECS:
        # An unrecognised model name falls back to the Venus E 3.0 shape
        # rather than the integration's 5000 W "no family limit" ceiling,
        # which no shipped Venus can actually deliver.
        return DeviceSpec(
            capacity_wh=BATTERY_CAPACITY_WH,
            max_charge_power=DEFAULT_MAX_CHARGE_POWER,
            max_discharge_power=DEFAULT_MAX_DISCHARGE_POWER,
        )
    capacity_wh, rated_power = _FAMILY_SPECS[family]
    return DeviceSpec(
        capacity_wh=capacity_wh,
        max_charge_power=rated_power,
        max_discharge_power=rated_power,
    )
