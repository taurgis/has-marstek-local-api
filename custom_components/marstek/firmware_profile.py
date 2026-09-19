"""Canonical firmware capability and wire-encoding profile."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class DeviceFamily(StrEnum):
    """Known Marstek Open API device families."""

    VENUS_A = "Venus A"
    VENUS_C = "Venus C"
    VENUS_D = "Venus D"
    VENUS_E = "Venus E"
    VENUS_E_MINI = "Venus E mini"
    UNKNOWN = "Unknown"


_REGULAR_FAMILIES = frozenset(
    {
        DeviceFamily.VENUS_A,
        DeviceFamily.VENUS_C,
        DeviceFamily.VENUS_D,
        DeviceFamily.VENUS_E,
    }
)
_KNOWN_FAMILIES = _REGULAR_FAMILIES | {DeviceFamily.VENUS_E_MINI}
_PV_FAMILIES = frozenset({DeviceFamily.VENUS_A, DeviceFamily.VENUS_D})


@dataclass(frozen=True, slots=True)
class FirmwareProfile:
    """Resolved device capabilities and wire-to-SI scaling."""

    family: DeviceFamily
    firmware_version: int | None
    supports_pv: bool
    supports_sys_dod: bool
    supports_sys_ble_advertising: bool
    supports_sys_led: bool
    supports_ups: bool
    max_manual_schedule_slot: int
    pv_energy_scale: float = 1.0
    pv_channel_1_power_scale: float = 0.1
    em_energy_scale: float = 1.0
    supports_em_energy: bool = False

    @property
    def firmware_known(self) -> bool:
        """Return whether discovery supplied a valid firmware integer."""
        return self.firmware_version is not None

    @property
    def setup_capability_signature(self) -> tuple[bool, bool, bool, bool, bool]:
        """Return setup-time availability flags, independent of firmware number."""
        return (
            self.supports_pv,
            self.supports_sys_dod,
            self.supports_sys_ble_advertising,
            self.supports_sys_led,
            self.supports_ups,
        )

    @property
    def control_generation(self) -> int | None:
        """Return the Control generation used for capability and safety gates."""
        return _control_generation(self.firmware_version)

    @property
    def openapi_reset_prone(self) -> bool:
        """Return whether Local API polling can factory-reset this firmware.

        VNSE3-0 Control v150's OTA note is "Optimized Local API send anomaly
        on Ethernet mode". A Venus E 3.0 user confirmed issue #15 is fixed
        after updating to 150. Builds below that generation (issues #14/#15),
        including dotted encoding 1476 (app 147.6), disable Open API and wipe
        settings under sustained UDP traffic. Unknown ``ver`` on a known
        family stays conservative.
        """
        if self.family not in _KNOWN_FAMILIES:
            return False
        generation = self.control_generation
        if generation is None:
            return True
        return generation < 150

    @property
    def parallel_requests_safe(self) -> bool:
        """Return whether parallel Open API calls are safe on this firmware."""
        return not self.openapi_reset_prone


_FAMILY_PATTERNS: tuple[tuple[DeviceFamily, re.Pattern[str]], ...] = (
    (
        DeviceFamily.VENUS_E_MINI,
        re.compile(r"^venus\s*e\s*mini(?:\s|$|\d)", re.IGNORECASE),
    ),
    (DeviceFamily.VENUS_A, re.compile(r"^venus\s*a(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_C, re.compile(r"^venus\s*c(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_D, re.compile(r"^venus\s*d(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_E, re.compile(r"^venus\s*e(?:\s|$|\d)", re.IGNORECASE)),
    # Vendor SKU discovery names; keep Venus* labels as the primary mapping.
    (DeviceFamily.VENUS_A, re.compile(r"^vnsa(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_D, re.compile(r"^vnsd(?:\s|$|\d)", re.IGNORECASE)),
    # VNSE3 is Venus E 3.x. Do not match VNSE2 (unsupported E2.0).
    (DeviceFamily.VENUS_E, re.compile(r"^vnse3(?:\s|$|\d)", re.IGNORECASE)),
)


def _normalize_family(device_type: str | None) -> DeviceFamily:
    """Normalize a discovery model name to a supported family."""
    if not isinstance(device_type, str):
        return DeviceFamily.UNKNOWN
    normalized = " ".join(re.sub(r"[-_]+", " ", device_type.strip()).split())
    for family, pattern in _FAMILY_PATTERNS:
        if pattern.match(normalized):
            return family
    return DeviceFamily.UNKNOWN


def _normalize_version(version: Any) -> int | None:
    """Normalize a non-negative Open API firmware integer.

    Discovery `ver` is an integer. App labels such as ``148.3`` share that
    integer as a dotted prefix; only the leading number selects the profile.
    """
    if isinstance(version, bool):
        return None
    if isinstance(version, int):
        return version if version >= 0 else None
    if isinstance(version, str):
        stripped = version.strip()
        if not stripped or not stripped.isascii():
            return None
        if stripped.isdecimal():
            return int(stripped, 10)
        dotted = re.fullmatch(r"([0-9]+)\.[0-9]+(?:\.[0-9]+)*", stripped)
        if dotted:
            return int(dotted.group(1), 10)
    return None


def extract_discovery_version(result: Mapping[str, Any] | None) -> Any:
    """Return raw discovery `ver` without substituting 0 for a missing field."""
    if not isinstance(result, Mapping) or "ver" not in result:
        return None
    return result["ver"]


def _control_generation(version: int | None) -> int | None:
    """Map Open API ``ver`` to the Control generation used for capability gates.

    VNSE3-0 Control **1476** is app firmware **147.6** (March 2026, before 148
    and 150). Four-digit values in 1000-1999 are that dotted encoding
    (147.6 -> 1476, and 150.9 would be 1509 if a device ever reported it).
    """
    if version is None:
        return None
    if 1000 <= version <= 1999:
        return version // 10
    return version


def resolve_firmware_profile_from_metadata(data: Mapping[str, Any]) -> FirmwareProfile:
    """Resolve capabilities from config-entry or discovery metadata."""
    return resolve_firmware_profile(data.get("device_type"), data.get("version"))


def resolve_firmware_profile(
    device_type: str | None,
    version: Any,
) -> FirmwareProfile:
    """Resolve capabilities and legacy encoding from discovery metadata."""
    family = _normalize_family(device_type)
    firmware_version = _normalize_version(version)
    firmware_known = firmware_version is not None
    generation = _control_generation(firmware_version)
    firmware_149 = generation is not None and generation >= 149
    firmware_150 = generation is not None and generation >= 150
    known_family = family in _KNOWN_FAMILIES
    regular_family = family in _REGULAR_FAMILIES
    supports_sys = (regular_family and firmware_150) or (
        family is DeviceFamily.VENUS_E_MINI and firmware_known
    )
    # Solar energy (#35) and PV1 power (#57) are independent encodings.
    # The Rev 3.1 PDF labels PV as watts; observed firmware does not.
    # GitHub issue wire samples (Open API integer `ver`, app labels mapped
    # to the leading integer: 147.7 → 147, 148.3 → 148, 150.9 → 150):
    #   Venus A 147 (#11, #20): solar Wh (often 0), PV1 deciwatts, GetMode CT
    #   keys present as zeros, EM energy keys present as 0, no bat_power.
    #   Venus A 148 / 148.3 (#28, #57): same as 147 for energy/PV1.
    #   Venus A 149 (#35): solar 0.01 kWh → Wh; PV1 still deciwatts; no SYS.
    #   Venus A 150.9 (#57): solar 0.01 kWh; PV1 still deciwatts; SYS/UPS.
    #   Venus E 144 (#21): GetMode without CT keys; grid energy in Wh;
    #   EM energy keys present as 0; no bat_power.
    #   Venus E 147/148 (#9, #14, #15, #25): legacy — no SYS/UPS (LED at 148
    #   is app-only until Open API ver >= 150).
    #   Venus E 150 (LAN capture / #34): SYS/UPS; GetMode CT keys zeros.
    #   Venus C 153 (#60): SYS/UPS, no PV; GetDevice may omit result MACs.
    scaled_pv_energy = known_family and (
        firmware_150 or (family is DeviceFamily.VENUS_A and firmware_149)
    )
    supports_em_energy = known_family and firmware_150

    return FirmwareProfile(
        family=family,
        firmware_version=firmware_version,
        supports_pv=family in _PV_FAMILIES,
        supports_sys_dod=supports_sys,
        supports_sys_ble_advertising=supports_sys,
        supports_sys_led=supports_sys,
        supports_ups=(regular_family or family is DeviceFamily.VENUS_E_MINI)
        and firmware_150,
        max_manual_schedule_slot=5
        if family is DeviceFamily.VENUS_E_MINI
        else 9,
        pv_energy_scale=10.0 if scaled_pv_energy else 1.0,
        pv_channel_1_power_scale=0.1,
        em_energy_scale=0.1 if supports_em_energy else 1.0,
        supports_em_energy=supports_em_energy,
    )


def firmware_profile_diagnostics(profile: FirmwareProfile) -> dict[str, Any]:
    """Return the firmware-profile section used in config-entry diagnostics."""
    return {
        "family": profile.family.value,
        "firmware_version": profile.firmware_version,
        "firmware_known": profile.firmware_known,
        "supports_pv": profile.supports_pv,
        "supports_sys_dod": profile.supports_sys_dod,
        "supports_sys_ble_advertising": profile.supports_sys_ble_advertising,
        "supports_sys_led": profile.supports_sys_led,
        "supports_ups": profile.supports_ups,
        "max_manual_schedule_slot": profile.max_manual_schedule_slot,
        "openapi_reset_prone": profile.openapi_reset_prone,
        "parallel_requests_safe": profile.parallel_requests_safe,
    }
