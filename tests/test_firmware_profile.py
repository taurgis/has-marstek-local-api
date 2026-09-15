"""Tests for the public firmware profile contract."""

from __future__ import annotations

import pytest

from custom_components.marstek.firmware_profile import (
    DeviceFamily,
    resolve_firmware_profile,
)


@pytest.mark.parametrize(
    ("device_type", "family", "supports_pv", "max_slot"),
    [
        ("VenusA", DeviceFamily.VENUS_A, True, 9),
        ("venus a 3.0", DeviceFamily.VENUS_A, True, 9),
        ("VenusC", DeviceFamily.VENUS_C, False, 9),
        ("Venus D Pro", DeviceFamily.VENUS_D, True, 9),
        ("VenusE 3.0", DeviceFamily.VENUS_E, False, 9),
        ("  VENUS E MINI 3.0 ", DeviceFamily.VENUS_E_MINI, False, 5),
        ("Some Energy Device", DeviceFamily.UNKNOWN, False, 9),
    ],
)
def test_profile_resolves_family_capabilities(
    device_type: str,
    family: DeviceFamily,
    supports_pv: bool,
    max_slot: int,
) -> None:
    """Resolve family capabilities from common discovery names."""
    profile = resolve_firmware_profile(device_type, 145)

    assert profile.family is family
    assert profile.supports_pv is supports_pv
    assert profile.max_manual_schedule_slot == max_slot


@pytest.mark.parametrize("version", [150, "150"])
@pytest.mark.parametrize("device_type", ["VenusA", "VenusC", "VenusD", "VenusE"])
def test_firmware_150_enables_sys_and_ups(
    device_type: str, version: int | str
) -> None:
    """Firmware 150 enables each gated feature on regular families."""
    profile = resolve_firmware_profile(device_type, version)

    assert profile.firmware_version == 150
    assert profile.firmware_known is True
    assert profile.supports_sys_dod is True
    assert profile.supports_sys_ble_advertising is True
    assert profile.supports_sys_led is True
    assert profile.supports_ups is True


@pytest.mark.parametrize("version", [145, 149])
def test_regular_legacy_firmware_has_no_sys_or_ups(version: int) -> None:
    """Firmware below 150 keeps gated features disabled."""
    profile = resolve_firmware_profile("VenusA", version)

    assert profile.supports_sys_dod is False
    assert profile.supports_sys_ble_advertising is False
    assert profile.supports_sys_led is False
    assert profile.supports_ups is False


def test_unknown_family_never_enables_sys_or_ups() -> None:
    """A recognized firmware number cannot authorize an unknown family."""
    profile = resolve_firmware_profile("Marstek Energy Storage", 150)

    assert profile.family is DeviceFamily.UNKNOWN
    assert profile.firmware_version == 150
    assert profile.supports_sys_dod is False
    assert profile.supports_sys_ble_advertising is False
    assert profile.supports_sys_led is False
    assert profile.supports_ups is False


def test_e_mini_known_firmware_sys_exception() -> None:
    """Any known E mini firmware enables SYS but gates UPS at 150."""
    legacy = resolve_firmware_profile("Venus E mini", 0)
    current = resolve_firmware_profile("VenusE-mini 3.0", 150)

    assert legacy.supports_sys_dod is True
    assert legacy.supports_sys_ble_advertising is True
    assert legacy.supports_sys_led is True
    assert legacy.supports_ups is False
    assert current.supports_ups is True


@pytest.mark.parametrize(
    "version",
    [None, True, False, -1, 145.0, "", " ", "v150", "not-a-version"],
)
def test_unknown_firmware_is_conservative(version: object) -> None:
    """Malformed firmware never authorizes SYS or UPS, including E mini."""
    profile = resolve_firmware_profile("Venus E mini", version)

    assert profile.firmware_version is None
    assert profile.firmware_known is False
    assert profile.supports_sys_dod is False
    assert profile.supports_sys_ble_advertising is False
    assert profile.supports_sys_led is False
    assert profile.supports_ups is False


def test_profile_exposes_legacy_encoding_contract() -> None:
    """All profiles preserve this ticket's legacy wire scaling."""
    profile = resolve_firmware_profile("VenusA", 150)

    assert profile.pv_energy_scale == 1.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.em_energy_scale == 1.0
    assert profile.supports_em_energy is False


def test_device_supports_pv_compatibility_facade_uses_profile() -> None:
    """The established PV API exposes the profile's family behavior."""
    from custom_components.marstek.const import device_supports_pv

    assert device_supports_pv("Venus D Pro") is True
    assert device_supports_pv("Venus E mini") is False
    assert device_supports_pv("NotVenusA") is False
