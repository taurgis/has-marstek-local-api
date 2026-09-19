"""Tests for the public firmware profile contract."""

from __future__ import annotations

import pytest

from custom_components.marstek.const import (
    device_default_socket_limit,
    get_device_power_limits,
)
from custom_components.marstek.firmware_profile import (
    DeviceFamily,
    extract_discovery_version,
    is_unsupported_venus_e2,
    resolve_firmware_profile,
    resolve_firmware_profile_from_metadata,
)


@pytest.mark.parametrize(
    ("device_type", "family", "supports_pv", "max_slot"),
    [
        ("VenusA", DeviceFamily.VENUS_A, True, 9),
        ("venus a 3.0", DeviceFamily.VENUS_A, True, 9),
        ("VenusC", DeviceFamily.VENUS_C, False, 9),
        ("Venus D Pro", DeviceFamily.VENUS_D, True, 9),
        ("VenusE 3.0", DeviceFamily.VENUS_E, False, 9),
        ("Venus E 3.0", DeviceFamily.VENUS_E, False, 9),
        ("  VENUS E MINI 3.0 ", DeviceFamily.VENUS_E_MINI, False, 5),
        ("VNSA-0", DeviceFamily.VENUS_A, True, 9),
        ("VNSD-0", DeviceFamily.VENUS_D, True, 9),
        ("VNSE3-0", DeviceFamily.VENUS_E, False, 9),
        ("vnse3 0", DeviceFamily.VENUS_E, False, 9),
        ("VNSE2-0", DeviceFamily.UNKNOWN, False, 9),
        ("Venus E2.0", DeviceFamily.UNKNOWN, False, 9),
        ("VenusE2.0", DeviceFamily.UNKNOWN, False, 9),
        ("Venus E2", DeviceFamily.UNKNOWN, False, 9),
        ("VenusE", DeviceFamily.UNKNOWN, False, 9),
        ("Venus E", DeviceFamily.UNKNOWN, False, 9),
        ("HMG-50", DeviceFamily.UNKNOWN, False, 9),
        ("HMG-25", DeviceFamily.UNKNOWN, False, 9),
        ("HMG-1", DeviceFamily.UNKNOWN, False, 9),
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
@pytest.mark.parametrize("device_type", ["VenusA", "VenusC", "VenusD", "VenusE 3.0"])
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
    assert profile.pv_energy_scale == 1.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.em_energy_scale == 1.0
    assert profile.supports_em_energy is False


def test_profile_exposes_legacy_encoding_contract() -> None:
    """Legacy firmware keeps unscaled Wh solar totals and channel-1 deciwatts."""
    profile = resolve_firmware_profile("VenusA", 145)

    assert profile.pv_energy_scale == 1.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.em_energy_scale == 1.0
    assert profile.supports_em_energy is False


@pytest.mark.parametrize("version", [145, 148, "148", "148.3"])
def test_venus_a_148_or_older_keeps_legacy_energy_and_deciwatt_pv(
    version: int | str,
) -> None:
    """Firmware 148 or older must keep 1.0.0 solar Wh and PV1 ÷10 (#57)."""
    profile = resolve_firmware_profile("VenusA", version)

    assert profile.firmware_version in {145, 148}
    assert profile.firmware_known is True
    assert profile.pv_energy_scale == 1.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.supports_ups is False
    assert profile.supports_sys_dod is False


def test_venus_a_149_uses_rev31_solar_energy_units() -> None:
    """Venus A firmware 149 already encodes solar energy as 0.01 kWh."""
    profile = resolve_firmware_profile("VenusA", 149)

    assert profile.pv_energy_scale == 10.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.em_energy_scale == 1.0
    assert profile.supports_em_energy is False
    assert profile.supports_ups is False
    assert profile.supports_sys_dod is False


def test_firmware_150_known_family_uses_rev31_energy_not_watt_pv() -> None:
    """Firmware 150+ scales solar/EM energy but keeps PV1 deciwatts (#57 / 150.9)."""
    profile = resolve_firmware_profile("VenusA", 150)

    assert profile.pv_energy_scale == 10.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.em_energy_scale == 0.1
    assert profile.supports_em_energy is True
    assert profile.supports_sys_dod is True
    assert profile.supports_ups is True


@pytest.mark.parametrize(
    ("version", "firmware_version", "pv_energy_scale", "pv_channel_1_power_scale"),
    [
        ("147.7", 147, 1.0, 0.1),
        ("148.3", 148, 1.0, 0.1),
        ("149.1", 149, 10.0, 0.1),
        ("150.9", 150, 10.0, 0.1),
    ],
)
def test_dotted_app_firmware_labels_use_leading_open_api_integer(
    version: str,
    firmware_version: int,
    pv_energy_scale: float,
    pv_channel_1_power_scale: float,
) -> None:
    """Dotted app labels use the leading integer; 150.9 keeps PV1 ÷10 (#57)."""
    profile = resolve_firmware_profile("VenusA", version)

    assert profile.firmware_version == firmware_version
    assert profile.pv_energy_scale == pv_energy_scale
    assert profile.pv_channel_1_power_scale == pv_channel_1_power_scale


@pytest.mark.parametrize(
    (
        "device_type",
        "version",
        "family",
        "pv_energy_scale",
        "supports_sys",
        "supports_ups",
        "supports_em_energy",
        "supports_pv",
        "max_slot",
    ),
    [
        ("VenusA", 147, DeviceFamily.VENUS_A, 1.0, False, False, False, True, 9),
        ("VenusA", "148.3", DeviceFamily.VENUS_A, 1.0, False, False, False, True, 9),
        ("VenusA", 149, DeviceFamily.VENUS_A, 10.0, False, False, False, True, 9),
        ("VenusA", "150.9", DeviceFamily.VENUS_A, 10.0, True, True, True, True, 9),
        ("VenusE 3.0", 144, DeviceFamily.VENUS_E, 1.0, False, False, False, False, 9),
        ("VenusE 3.0", 147, DeviceFamily.VENUS_E, 1.0, False, False, False, False, 9),
        ("VenusE 3.0", 148, DeviceFamily.VENUS_E, 1.0, False, False, False, False, 9),
        ("VenusE 3.0", 150, DeviceFamily.VENUS_E, 10.0, True, True, True, False, 9),
        ("VenusC", 153, DeviceFamily.VENUS_C, 10.0, True, True, True, False, 9),
        ("Venus E mini", 145, DeviceFamily.VENUS_E_MINI, 1.0, True, False, False, False, 5),
        ("VenusD", 145, DeviceFamily.VENUS_D, 1.0, False, False, False, True, 9),
    ],
)
def test_github_issue_firmware_versions_resolve_observed_capabilities(
    device_type: str,
    version: int | str,
    family: DeviceFamily,
    pv_energy_scale: float,
    supports_sys: bool,
    supports_ups: bool,
    supports_em_energy: bool,
    supports_pv: bool,
    max_slot: int,
) -> None:
    """Every firmware version reported in GitHub issues maps to the right profile."""
    profile = resolve_firmware_profile(device_type, version)

    assert profile.family is family
    assert profile.pv_energy_scale == pv_energy_scale
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.supports_sys_dod is supports_sys
    assert profile.supports_sys_ble_advertising is supports_sys
    assert profile.supports_sys_led is supports_sys
    assert profile.supports_ups is supports_ups
    assert profile.supports_em_energy is supports_em_energy
    assert profile.supports_pv is supports_pv
    assert profile.max_manual_schedule_slot == max_slot


def test_legacy_venus_d_keeps_deciwatt_pv_and_wh_solar() -> None:
    """Venus D below 150 keeps channel-1 deciwatts and unscaled solar Wh."""
    profile = resolve_firmware_profile("VenusD", 145)

    assert profile.pv_energy_scale == 1.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.supports_em_energy is False


def test_unknown_family_does_not_guess_rev31_scaling() -> None:
    """A recognized firmware number cannot authorize Rev 3.1 wire units."""
    profile = resolve_firmware_profile("Marstek Energy Storage", 150)

    assert profile.family is DeviceFamily.UNKNOWN
    assert profile.pv_energy_scale == 1.0
    assert profile.pv_channel_1_power_scale == 0.1
    assert profile.em_energy_scale == 1.0
    assert profile.supports_em_energy is False


def test_missing_discovery_ver_is_unknown_not_zero() -> None:
    """A payload without `ver` must not be treated as firmware 0."""
    raw = extract_discovery_version({"device": "Venus E mini", "ble_mac": "aabbccddeeff"})

    assert raw is None
    profile = resolve_firmware_profile("Venus E mini", raw)
    assert profile.firmware_known is False
    assert profile.supports_sys_dod is False
    assert profile.supports_ups is False


def test_present_discovery_ver_zero_is_a_known_integer() -> None:
    """An explicit ver of 0 is a known integer, not a missing field."""
    raw = extract_discovery_version({"ver": 0})

    assert raw == 0
    profile = resolve_firmware_profile("Venus E mini", raw)
    assert profile.firmware_known is True
    assert profile.supports_sys_dod is True
    assert profile.supports_ups is False


def test_device_supports_pv_compatibility_facade_uses_profile() -> None:
    """The established PV API exposes the profile's family behavior."""
    from custom_components.marstek.const import device_supports_pv

    assert device_supports_pv("Venus D Pro") is True
    assert device_supports_pv("Venus E mini") is False
    assert device_supports_pv("NotVenusA") is False


def test_setup_capability_signature_ignores_firmware_number_and_label() -> None:
    """150 and 151 plus equivalent model names share setup-time capabilities."""
    profile_150 = resolve_firmware_profile("VenusE 3.0", 150)
    profile_151 = resolve_firmware_profile("Venus E 3.0", 151)

    assert profile_150.setup_capability_signature == profile_151.setup_capability_signature
    assert profile_150.firmware_version != profile_151.firmware_version


def test_setup_capability_signature_changes_when_ups_and_sys_unlock() -> None:
    """Venus E 149 and 150 differ in setup-time UPS and SYS availability."""
    legacy = resolve_firmware_profile("VenusE 3.0", 149)
    current = resolve_firmware_profile("VenusE 3.0", 150)

    assert legacy.setup_capability_signature != current.setup_capability_signature
    assert legacy.supports_ups is False
    assert current.supports_ups is True
    assert legacy.supports_sys_dod is False
    assert current.supports_sys_dod is True


def test_unparseable_firmware_uses_legacy_safe_setup_signature() -> None:
    """Unknown firmware matches the conservative pre-150 setup capabilities."""
    unknown = resolve_firmware_profile("VenusE 3.0", "not-a-version")
    legacy = resolve_firmware_profile("VenusE 3.0", 149)

    assert unknown.setup_capability_signature == legacy.setup_capability_signature
    assert unknown.supports_ups is False
    assert unknown.supports_sys_dod is False


def test_setup_capability_signature_changes_when_pv_family_appears() -> None:
    """Model changes that unlock PV sensors are setup-capability changes."""
    venus_e = resolve_firmware_profile("VenusE 3.0", 150)
    venus_a = resolve_firmware_profile("VenusA 3.0", 150)

    assert venus_e.setup_capability_signature != venus_a.setup_capability_signature
    assert venus_e.supports_pv is False
    assert venus_a.supports_pv is True


def test_resolve_firmware_profile_from_metadata_uses_device_type_and_version() -> None:
    """Merged config-entry/discovery dicts resolve through the canonical helper."""
    profile = resolve_firmware_profile_from_metadata(
        {"device_type": "VenusE 3.0", "version": "150"}
    )

    assert profile.family is DeviceFamily.VENUS_E
    assert profile.firmware_version == 150
    assert profile.supports_ups is True
    assert profile.openapi_reset_prone is False
    assert profile.parallel_requests_safe is True


def test_vnse3_1476_is_legacy_reset_prone() -> None:
    """Control 1476 is app 147.6 and must not be treated as newer than 150."""
    profile = resolve_firmware_profile("VNSE3-0", 1476)

    assert profile.firmware_version == 1476
    assert profile.control_generation == 147
    assert profile.supports_sys_dod is False
    assert profile.supports_ups is False
    assert profile.openapi_reset_prone is True
    assert profile.parallel_requests_safe is False


@pytest.mark.parametrize(
    ("device_type", "version", "reset_prone"),
    [
        ("VenusE 3.0", 144, True),
        ("VenusE 3.0", 147, True),
        ("VenusE 3.0", 149, True),
        ("VenusE 3.0", 150, False),
        ("VenusA", 148, True),
        ("VenusA", 150, False),
        ("Venus E mini", 145, True),
        ("Venus E mini", "not-a-version", True),
        ("Marstek Energy Storage", 144, True),
        ("Marstek Energy Storage", 150, False),
        ("Marstek Energy Storage", 3, False),
        ("Marstek Energy Storage", 99, False),
        ("Marstek Energy Storage", 100, True),
        ("Marstek Energy Storage", 149, True),
        ("Marstek Energy Storage", 150, False),
        ("Marstek Energy Storage", 1476, True),
        ("Marstek Energy Storage", 1509, False),
        ("VenusE 3.0", "147.6", True),
        ("Venus E2.0", 150, False),
        ("Venus E2.0", 144, True),
        ("VenusE", 153, False),
        ("HMG-50", 156, False),
        ("HMG-50", 146, True),
    ],
)
def test_openapi_reset_prone_follows_control_generation(
    device_type: str, version: int | str, reset_prone: bool
) -> None:
    """Issue #15 is firmware-side; generation < 150 stays sequential-only."""
    profile = resolve_firmware_profile(device_type, version)

    assert profile.openapi_reset_prone is reset_prone
    assert profile.parallel_requests_safe is not reset_prone


@pytest.mark.parametrize(
    ("device_type", "max_discharge", "socket_default"),
    [
        ("VenusA", 1500, False),
        ("VNSA-0", 1500, False),
        ("VenusD", 2200, True),
        ("VNSD-0", 2200, True),
        ("VenusE 3.0", 2500, True),
        ("VNSE3-0", 2500, True),
        ("VNSE2-0", 5000, False),
        ("VenusE", 5000, False),
        ("Venus E2.0", 5000, False),
        ("HMG-50", 5000, False),
    ],
)
def test_sku_and_venus_names_share_power_limits(
    device_type: str, max_discharge: int, socket_default: bool
) -> None:
    """Vendor SKUs use the same family power limits as Venus display names."""
    _min_charge, max_power = get_device_power_limits(device_type)

    assert max_power == max_discharge
    assert device_default_socket_limit(device_type) is socket_default


@pytest.mark.parametrize(
    "device_type",
    [
        "Venus E2.0",
        "VenusE2.0",
        "Venus E2",
        "VNSE2-0",
        "VenusE",
        "Venus E",
        "HMG-50",
        "HMG-25",
        "HMG-1",
        "hmg50",
    ],
)
def test_venus_e2_is_unsupported_and_not_venus_e(device_type: str) -> None:
    """HMG-50 / bare VenusE GetDevice names must not unlock Venus E 3.x."""
    assert is_unsupported_venus_e2(device_type) is True
    profile = resolve_firmware_profile(device_type, 150)

    assert profile.family is DeviceFamily.UNKNOWN
    assert profile.supports_sys_dod is False
    assert profile.supports_ups is False
    assert profile.openapi_reset_prone is False


@pytest.mark.parametrize("device_type", ["Venus E 3.0", "VenusE 3.0", "VNSE3-0"])
def test_venus_e3_is_not_classified_as_e2(device_type: str) -> None:
    """Venus E 3.x discovery names stay on the supported Venus E family."""
    assert is_unsupported_venus_e2(device_type) is False
    assert resolve_firmware_profile(device_type, 150).family is DeviceFamily.VENUS_E
