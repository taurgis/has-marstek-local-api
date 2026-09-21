"""Canonical firmware capability and wire-encoding profile."""

from __future__ import annotations

import math
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
# Observed Control Open API ``ver`` values start near 144. Unknown model
# names with a plausible Control generation below 150 still get the reset
# warning (mis-parsed Venus). Placeholders such as ``version: 3`` do not.
_UNKNOWN_CONTROL_GENERATION_MIN = 100
# HMG-50 Control starts at generation 153. The community archives hold
# 153 / 155 / 156 and the 153 strings also cover 154, so this is a floor
# rather than a list: an unarchived 154, or a future 157, is still HMG-50
# and must not fall through to the regular Venus C profile. The same
# binaries can GetDevice as ``VenusE`` (unsupported E 2.x) or ``VenusC``
# (issue #60). Recv-list and OTA notes are shared.
_HMG50_CONTROL_MIN_GENERATION = 153
_HMG50_EM_SERVER_GENERATION = 155
_HMG50_OPENAPI_STABLE_GENERATION = 156
# HMG-50 Control shares one Wi-Fi receive channel between the Local API
# server and its own UDP meter client, so Open API traffic competes with
# the regulation loop that Auto mode depends on. See
# ``tools/firmware/HMG50_METER_CHANNEL.md`` for the string evidence: every
# archived HMG-50 image (153/155/156) filters exactly one Quectel URC,
# ``+QIURC: "recv",3`` -- the same connect id the Local API server opens
# (``+QIOPEN: 3,0`` next to ``UDP server open!``) -- and drains it with
# buffered ``AT+QIRD``. VNSE3-0 / VNSA-0 / VNSD-0 carry a generic
# ``+QIURC: "recv",%d,`` parser and, from Control generation 149, a second
# hard-coded channel (``+QIURC: "recv",11``). No HMG-50 build has it, 156
# included, so this is not something a firmware update has cleared.
# Venus E 2.x / HMG-50 is not Venus E 3.x. HMG-50 Control 153+ Open API
# GetDevice reports ``device: "VenusE"`` (src ``VenusE-%s``), not
# ``Venus E2.0`` / ``VNSE2``. Bare ``VenusE`` without 3.x must not unlock
# the VNSE3-0 family. VNSE3-0 reports ``VenusE 3.0``.
_VENUS_E2_PATTERN = re.compile(
    r"^(?:"
    r"venus\s*e\s*2(?:\.\d+)?(?:\s|$)|"
    r"vnse2(?:\s|$|\d)|"
    r"hmg(?:\s|$|\d)|"
    r"venus\s*e$"
    r")",
    re.IGNORECASE,
)


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
    supports_em_status: bool = False
    hmg50_control: bool = False

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
    def setup_reload_signature(self) -> tuple[object, ...]:
        """Return profile values that require a config-entry reload when they change.

        Capability gates recreate SYS/UPS/PV entities. Reset-prone also
        recreates (or removes) Bat.GetStatus entities and the Open API warning.

        The scales and the EM/schedule gates matter for the same reason: the
        coordinator, the parser and the services all read the profile captured
        at setup. A Venus A moving 148 -> 149 changes ``pv_energy_scale`` from
        1.0 to 10.0, so without a reload the entry would keep decoding PV
        energy ten times too small until Home Assistant restarts.
        """
        return (
            *self.setup_capability_signature,
            self.openapi_reset_prone,
            self.pv_energy_scale,
            self.pv_channel_1_power_scale,
            self.em_energy_scale,
            self.supports_em_status,
            self.max_manual_schedule_slot,
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
        family stays conservative. Unknown model names with a Control-like
        generation below 150 are also treated as reset-prone.

        HMG-50 Control (Venus C 153/155/156 and bare ``VenusE`` at those
        generations) publishes an Open API stability fix at **156**, not 150.
        """
        generation = self.control_generation
        if self.hmg50_control:
            return generation is None or generation < _HMG50_OPENAPI_STABLE_GENERATION
        if self.family not in _KNOWN_FAMILIES:
            return generation is not None and _UNKNOWN_CONTROL_GENERATION_MIN <= generation < 150
        if generation is None:
            return True
        return generation < 150

    @property
    def shared_meter_udp_channel(self) -> bool:
        """Return whether Open API reads contend with the device meter client.

        HMG-50 Control (Venus C 2.0 and the bare ``VenusE`` of Venus E 2.0)
        has a single inbound Wi-Fi channel for both the Local API server and
        its own UDP meter polling. Losing meter samples to Open API
        traffic is what makes the device declare the meter gone and stop
        self-consumption charging (issue #82), and it is the same defect
        behind Marstek's own "Venus E2.0 may disconnect from CT003" warning.
        Nothing in the client can repair it; keep the traffic minimal.
        """
        return self.hmg50_control

    @property
    def parallel_requests_safe(self) -> bool:
        """Return whether parallel Open API calls are safe on this firmware."""
        if self.shared_meter_udp_channel:
            return False
        return not self.openapi_reset_prone

    @property
    def openapi_wifi_retransmit_safe(self) -> bool:
        """Return whether extra Wi-Fi UDP copies are allowed.

        Application retries are RFC 1122's job, but extra datagrams are
        what reset-prone Control used to disable Local API. Opt in only
        after a known family and Control generation that this profile
        already treats as not reset-prone. Unknown models and missing
        ``ver`` stay one-shot, and so does HMG-50 Control: a second copy
        there lands on the channel its meter client is sharing.
        """
        if self.openapi_reset_prone or self.shared_meter_udp_channel:
            return False
        if self.family not in _KNOWN_FAMILIES:
            return False
        return self.control_generation is not None


_FAMILY_PATTERNS: tuple[tuple[DeviceFamily, re.Pattern[str]], ...] = (
    (
        DeviceFamily.VENUS_E_MINI,
        re.compile(r"^venus\s*e\s*mini(?:\s|$|\d)", re.IGNORECASE),
    ),
    (DeviceFamily.VENUS_A, re.compile(r"^venus\s*a(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_C, re.compile(r"^venus\s*c(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_D, re.compile(r"^venus\s*d(?:\s|$|\d)", re.IGNORECASE)),
    # Require 3.x so bare ``VenusE`` (HMG-50 GetDevice) is not Venus E 3.0.
    (DeviceFamily.VENUS_E, re.compile(r"^venus\s*e\s*3(?:\s|$|\.)", re.IGNORECASE)),
    # Vendor SKU discovery names; keep Venus* labels as the primary mapping.
    (DeviceFamily.VENUS_A, re.compile(r"^vnsa(?:\s|$|\d)", re.IGNORECASE)),
    (DeviceFamily.VENUS_D, re.compile(r"^vnsd(?:\s|$|\d)", re.IGNORECASE)),
    # VNSE3 is Venus E 3.x. Do not match VNSE2 (unsupported E2.0).
    (DeviceFamily.VENUS_E, re.compile(r"^vnse3(?:\s|$|\d)", re.IGNORECASE)),
)


def is_unsupported_venus_e2(device_type: str | None) -> bool:
    """Return True for Venus E 2.x / HMG-50 names this integration does not support."""
    if not isinstance(device_type, str):
        return False
    normalized = " ".join(re.sub(r"[-_]+", " ", device_type.strip()).split())
    return bool(_VENUS_E2_PATTERN.match(normalized))


def _normalize_family(device_type: str | None) -> DeviceFamily:
    """Normalize a discovery model name to a supported family."""
    if not isinstance(device_type, str):
        return DeviceFamily.UNKNOWN
    if is_unsupported_venus_e2(device_type):
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
    A build that puts that label on the wire as a JSON *number* rather than a
    string means the same thing, so ``150.9`` resolves like ``"150.9"``.
    Rejecting it instead would silently downgrade a Rev 3.1 device: unknown
    ``ver`` counts as reset-prone, which drops the SYS/UPS entities, forces
    serialized polling, and raises the firmware-reset repair warning.
    """
    if isinstance(version, bool):
        return None
    if isinstance(version, int):
        return version if version >= 0 else None
    if isinstance(version, float):
        if not math.isfinite(version) or version < 0:
            return None
        return int(version)
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


def _is_hmg50_control_image(
    device_type: str | None,
    family: DeviceFamily,
    generation: int | None,
) -> bool:
    """Return whether this discovery runs an HMG-50 Control image.

    Generation is a floor, not a membership test: 154 is unarchived but
    shares the 153 strings, and a future 157 keeps the same recv list.
    Falling through to the regular Venus C profile would hand those builds
    the SYS/UPS entities HMG-50 does not serve.
    """
    if generation is None or generation < _HMG50_CONTROL_MIN_GENERATION:
        return False
    return family is DeviceFamily.VENUS_C or is_unsupported_venus_e2(device_type)


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
    hmg50_control = _is_hmg50_control_image(device_type, family, generation)
    # HMG-50 Control has no Open API SYS/UPS in 153/155/156 (recv list is
    # GetDevice, ES.*, BLE, Wifi, Bat, PV stub, and EM from 155). Do not
    # unlock SYS from string presence in VNSE3-0 147-149 either: HA keeps
    # the Rev 3.1 ``ver >= 150`` gate (PDF + issue #15).
    supports_sys = (regular_family and firmware_150 and not hmg50_control) or (
        family is DeviceFamily.VENUS_E_MINI and firmware_known
    )
    supports_ups = (
        (regular_family or family is DeviceFamily.VENUS_E_MINI)
        and firmware_150
        and not hmg50_control
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
    #   Venus C 153 (#60): HMG-50 reporting VenusC; no SYS/UPS; GetDevice
    #   may omit result MACs; EM.GetStatus is meter-client only until 155.
    scaled_pv_energy = known_family and (
        firmware_150 or (family is DeviceFamily.VENUS_A and firmware_149)
    )
    if is_unsupported_venus_e2(device_type) or (family is DeviceFamily.VENUS_C and hmg50_control):
        supports_em_status = generation is not None and generation >= _HMG50_EM_SERVER_GENERATION
    else:
        supports_em_status = known_family
    supports_em_energy = supports_em_status and firmware_150

    return FirmwareProfile(
        family=family,
        firmware_version=firmware_version,
        supports_pv=family in _PV_FAMILIES,
        supports_sys_dod=supports_sys,
        supports_sys_ble_advertising=supports_sys,
        supports_sys_led=supports_sys,
        supports_ups=supports_ups,
        max_manual_schedule_slot=5 if family is DeviceFamily.VENUS_E_MINI else 9,
        pv_energy_scale=10.0 if scaled_pv_energy else 1.0,
        pv_channel_1_power_scale=0.1,
        em_energy_scale=0.1 if supports_em_energy else 1.0,
        supports_em_energy=supports_em_energy,
        supports_em_status=supports_em_status,
        hmg50_control=hmg50_control,
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
        "supports_em_status": profile.supports_em_status,
        "max_manual_schedule_slot": profile.max_manual_schedule_slot,
        "control_generation": profile.control_generation,
        "openapi_reset_prone": profile.openapi_reset_prone,
        "parallel_requests_safe": profile.parallel_requests_safe,
        "openapi_wifi_retransmit_safe": profile.openapi_wifi_retransmit_safe,
        "shared_meter_udp_channel": profile.shared_meter_udp_channel,
    }
