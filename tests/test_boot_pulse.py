"""The boot self-test a linked plug shows, told apart from ordinary charging.

The sequences follow a user's plug history at 5 s resolution; see
``custom_components/marstek/helpers/boot_pulse.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.helpers.boot_pulse import (
    BootPulseDetector,
    async_track_boot_pulses,
    plug_power_entity,
)

_STEP = 5.0


def _replay(watts: Sequence[float], *, start: float = 1000.0) -> list[float]:
    detector = BootPulseDetector()
    found = []
    for index, value in enumerate(watts):
        if (at := detector.feed(start + index * _STEP, value)) is not None:
            found.append(at)
    return found


def test_standby_gone_burst_then_quiet_is_a_self_test() -> None:
    """2026-10-18 04:00: 8 W, quiet, 2.5 kW for 20 s, quiet, 8 W."""
    found = _replay([9, 7, 1, 0, 1, 2550, 2234, 2156, 2203, 1, 8, 9])
    assert found == [1000.0 + 5 * _STEP]


def test_export_pulse_then_burst_needs_no_quiet_after() -> None:
    """2026-10-21 22:41: the unit goes straight back to charging after the test."""
    found = _replay([9, -533, -1276, 0, 2348, 1008, 89, 60, 153, 131])
    assert found == [1000.0 + 4 * _STEP]


def test_ordinary_charging_is_not_a_self_test() -> None:
    """A charge starts from standby, not from a quiet plug, and runs for minutes."""
    assert _replay([8, 8, 400, 1800, 2500, *[2500] * 30, 600, 8]) == []


def test_a_long_burst_from_quiet_is_a_charge() -> None:
    """Quiet first, but longer than a minute at full power."""
    assert _replay([8, 0, *[2550] * 14, 0, 8]) == []


def test_bursts_that_do_not_close_are_dropped() -> None:
    """No quiet reading after the burst, a burst too weak, or quiet that comes too late."""
    assert _replay([8, 0, 2550, 2200, 900, 900, 900, 900, 900, 900, 900, 900]) == []
    assert _replay([8, 0, 1600, 1600, 0, 8]) == []
    assert _replay([8, 0, 2550, 100, *[100] * 7, 0]) == []


def test_two_tests_in_a_row_are_two() -> None:
    """2026-10-26 08:03 and 08:05: a boot loop shows the test twice."""
    one = [8, 6, 7, 1, 2549, 1, 8]
    assert len(_replay([*one, *[8] * 14, *one])) == 2


async def test_power_sensor_found_and_followed(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The plug's power sensor is the shortest power id on the energy sensor's device."""
    plug_entry = MockConfigEntry(domain="homewizard")
    plug_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=plug_entry.entry_id, identifiers={("homewizard", "plug")}
    )
    registry = er.async_get(hass)

    def sensor(key: str, device_class: str) -> str:
        return registry.async_get_or_create(
            "sensor",
            "homewizard",
            key,
            device_id=device.id,
            suggested_object_id=key,
            original_device_class=device_class,
        ).entity_id

    energy = sensor("plug_energy_import", "energy")
    sensor("plug_power_phase_1", "power")
    power = sensor("plug_power", "power")

    assert plug_power_entity(hass, energy) == power
    assert plug_power_entity(hass, None) is None
    assert plug_power_entity(hass, "sensor.not_registered") is None

    found: list[float] = []
    stop = async_track_boot_pulses(hass, power, found.append)
    for value, unit in [
        ("8", "W"),
        ("0", "W"),
        ("unavailable", "W"),
        ("2.55", "kW"),
        ("2200", "W"),
        ("1", "W"),
    ]:
        hass.states.async_set(power, value, {"unit_of_measurement": unit})
        await hass.async_block_till_done()
        freezer.tick(_STEP)
    hass.states.async_remove(power)
    await hass.async_block_till_done()
    stop()

    assert len(found) == 1
