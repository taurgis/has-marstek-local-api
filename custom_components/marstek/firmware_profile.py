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


_FAMILY_PATTERNS: tuple[tuple[DeviceFamily, re.Pattern[str]], ...] = (
    (
        DeviceFamily.VENUS_E_MINI,
        re.compile(r"^venus\s*e\s*mini(?:\s|$|\d)", re.IGNORECASE),
    ),
    (DeviceFamily.VENUS_A, re.compile(r"^venus\s*a(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_C, re.compile(r"^venus\s*c(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_D, re.compile(r"^venus\s*d(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_E, re.compile(r"^venus\s*e(?:\s|$|\d)", re.IGNORECASE)),
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
    """Normalize a non-negative integer firmware version."""
    if isinstance(version, bool):
        return None
    if isinstance(version, int):
        return version if version >= 0 else None
    if isinstance(version, str):
        stripped = version.strip()
        if stripped and stripped.isascii() and stripped.isdecimal():
            return int(stripped, 10)
    return None


def extract_discovery_version(result: Mapping[str, Any] | None) -> Any:
    """Return raw discovery `ver` without substituting 0 for a missing field."""
    if not isinstance(result, Mapping) or "ver" not in result:
        return None
    return result["ver"]


def resolve_firmware_profile(
    device_type: str | None,
    version: Any,
) -> FirmwareProfile:
    """Resolve capabilities and legacy encoding from discovery metadata."""
    family = _normalize_family(device_type)
    firmware_version = _normalize_version(version)
    firmware_known = firmware_version is not None
    firmware_149 = firmware_version is not None and firmware_version >= 149
    firmware_150 = firmware_version is not None and firmware_version >= 150
    known_family = family in _KNOWN_FAMILIES
    regular_family = family in _REGULAR_FAMILIES
    supports_sys = (regular_family and firmware_150) or (
        family is DeviceFamily.VENUS_E_MINI and firmware_known
    )
    scaled_pv_energy = known_family and (
        firmware_150 or (family is DeviceFamily.VENUS_A and firmware_149)
    )
    watt_pv_channels = family in _PV_FAMILIES and firmware_150
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
        pv_channel_1_power_scale=1.0 if watt_pv_channels else 0.1,
        em_energy_scale=0.1 if supports_em_energy else 1.0,
        supports_em_energy=supports_em_energy,
    )
