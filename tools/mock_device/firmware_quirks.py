"""Control-firmware wire quirks used only by the mock device.

Home Assistant capability gates stay in ``firmware_profile.py``. These helpers
encode recv-list and field-table evidence from archived Control blobs so the
mock answers the way those images dispatch Open API methods.
"""

from __future__ import annotations

from typing import Any

from custom_components.marstek.firmware_profile import DeviceFamily, FirmwareProfile

# ``Set.Ver`` / ``Reset.Factory`` join the recv list at Control generation 149.
_SET_VER_MIN_GENERATION = 149
# The archived VNSA-0 blob ``ver=1487`` (app 148.7) already ships them, one
# generation before the rest of the catalog. Plain 148 does not.
_VNSA_EARLY_SET_VER_VERSION = 1487


def reports_es_bat_power(profile: FirmwareProfile) -> bool:
    """Return whether ES.GetStatus includes ``bat_power``.

    The field string exists only in HMG-50 Control **153**. It is absent from
    155/156 and from every archived VNSE3-0 / VNSA-0 / VNSD-0 image. Venus E
    3.0 live captures omit ``bat_power``.
    """
    return profile.hmg50_control and profile.control_generation == 153


def supports_wifi_set_config(profile: FirmwareProfile) -> bool:
    """Return whether ``Wifi.SetConfig`` is on the Open API recv list.

    HMG-50 Control 153/155/156 include it. VNSE3-0 / VNSA-0 / VNSD-0 do not.
    """
    return profile.hmg50_control


def supports_set_ver_and_factory_reset(profile: FirmwareProfile) -> bool:
    """Return whether ``Set.Ver`` / ``Reset.Factory`` are on the recv list.

    Present from generation **149** on VNSE3-0 / VNSA-0 / VNSD-0, which
    covers VenusE Pro **1508** (app 150.8), plus the archived VNSA-0 **1487**
    (app 148.7) that carries them one generation early. HMG-50 never has
    them. Venus E mini is not in the Control catalog; do not invent the
    methods.

    Gate on the Control generation, not the raw ``ver``: ``1476`` (app 147.6)
    is generation 147 and a plain ``>= 149`` on the raw integer would wrongly
    accept it.
    """
    if profile.hmg50_control or profile.family is DeviceFamily.VENUS_E_MINI:
        return False
    if profile.firmware_version == _VNSA_EARLY_SET_VER_VERSION:
        return True
    generation = profile.control_generation
    if generation is None:
        return False
    return generation >= _SET_VER_MIN_GENERATION


def pv_method_not_found_extra_data(profile: FirmwareProfile) -> int | None:
    """Return the captured PV.GetStatus ``error.data`` for non-PV firmware.

    Venus E 3.0 firmware **150** LAN capture is ``-32601`` with ``data: 424``.
    Use Control generation so ``ver=1476`` (app 147.6) does not inherit that
    150-only payload. Other non-PV families have no matching capture.
    """
    generation = profile.control_generation
    if profile.family is DeviceFamily.VENUS_E and generation is not None and generation >= 150:
        return 424
    return None


# From Control 151 the reply ``src`` names the SKU instead of the product:
# VNSE3-0 / VNSD-0 151 answer ``"VNSE3-0-<ble_mac>"`` where 150 sent
# ``"VenusE 3.0-<ble_mac>"`` (`` VenusE 3.0-%s`` became ``%s-%s`` next to the
# SKU literal). GetDevice ``device`` keeps the product name.
#
# Venus A 1509 (app 150.9) already made that switch even though ``ver`` 1509
# still folds to generation 150: ``VenusA-%s`` became ``%s-%s`` next to
# ``VNSA-0``. VNSA-0 150 and the 1491 alternate (app 149.1, generation 149)
# still have ``VenusA-%s``. VNSA-0 151 follows the generation >= 151 rule, so
# its ``src`` is ``VNSA-0-<ble>`` as well. VNSA-0 1508 banners VEPRO-0 /
# VenusE Pro (unknown family); its src site is ``%s-%s`` next to the product
# name ``VenusE Pro``, not the SKU ``VNSA-0``.
_SKU_SRC_MIN_GENERATION = 151
_VNSA_SKU_SRC_MIN_VERSION = 1509
_SRC_SKUS = {
    DeviceFamily.VENUS_E: "VNSE3-0",
    DeviceFamily.VENUS_D: "VNSD-0",
    DeviceFamily.VENUS_A: "VNSA-0",
}


def _uses_sku_src(profile: FirmwareProfile) -> bool:
    """Return whether this image formats ``src`` as ``<SKU>-<ble_mac>``."""
    generation = profile.control_generation
    if generation is not None and generation >= _SKU_SRC_MIN_GENERATION:
        return True
    version = profile.firmware_version
    return (
        profile.family is DeviceFamily.VENUS_A
        and version is not None
        and version >= _VNSA_SKU_SRC_MIN_VERSION
    )


def openapi_src_prefix(profile: FirmwareProfile, device_name: str) -> str:
    """Return the name the firmware puts before the BLE MAC in ``src``."""
    sku = _SRC_SKUS.get(profile.family)
    if sku is None or not _uses_sku_src(profile):
        return device_name
    return sku


def answers_unknown_methods(profile: FirmwareProfile) -> bool:
    """Return whether an unsupported method gets a ``-32601`` reply.

    Control firmware answers it. A Venus E mini ``VNSEM-0`` 301 (issue #86)
    sent nothing for ``PV.GetStatus`` or ``Foo.Get`` within 10 s and kept
    answering other requests, so it drops them silently.
    """
    return profile.family is not DeviceFamily.VENUS_E_MINI


# Issue #104, reported on real VNSEM-0 firmware 301. Not a capture of a
# command the Marstek app completed with a numeric power. Issue #86's
# capture is reads and UDP rules only. The device answered ES.SetMode
# Passive with JSON-RPC id 0 (not the request id), code -32700, data 401.
_VNSEM_REPORTED_SETMODE_VERSION = 301
_VNSEM_REPORTED_PASSIVE_PARSE_DATA = 401


def vnsem_301_setmode_report(profile: FirmwareProfile) -> bool:
    """Return whether to emulate the issue #104 VNSEM-0 301 SetMode report.

    Manual keeps time, weekday and enable and answers ``set_result: true``,
    while power is left unset unless the payload also has Rev 3.1
    ``manual_set`` 0-2 with a non-negative ``power`` (documentation-based;
    not a capture). Negative ``power`` with ``manual_set``, and
    ``manual_set`` 3 (auto), are unknown and left unset. Passive is
    rejected with a parse error whose JSON-RPC id is 0. Other Venus E mini
    versions keep the generic simulator.
    """
    return (
        profile.family is DeviceFamily.VENUS_E_MINI
        and profile.firmware_version == _VNSEM_REPORTED_SETMODE_VERSION
    )


def documented_manual_set(config: dict[str, Any]) -> int | None:
    """Return a Rev 3.1 ``manual_set`` the mock is willing to apply.

    Documentation-based, not captured. Accepts 0 disable, 1 charge, 2
    discharge with a non-negative ``power`` (the official example is
    ``power`` 100 with ``manual_set`` 1). ``manual_set`` 3 (auto) and a
    negative ``power`` are unknown — not applied — so the mock cannot
    silently treat +100 and -100 as the same write.
    """
    value = config.get("manual_set")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value not in (0, 1, 2):
        return None
    power = config.get("power")
    if isinstance(power, bool) or not isinstance(power, int) or power < 0:
        return None
    return value


def undocumented_manual_set_reason(config: dict[str, Any]) -> str | None:
    """Return why a ``manual_set`` payload is unknown, or None if omitted."""
    if "manual_set" not in config:
        return None
    if documented_manual_set(config) is not None:
        return None
    value = config.get("manual_set")
    power = config.get("power")
    if value == 3:
        return "manual_set 3 (auto) is documented in Rev 3.1 but not captured"
    if isinstance(power, int) and not isinstance(power, bool) and power < 0:
        return "negative power with manual_set is not the Rev 3.1 unsigned example"
    return "manual_set is present but not a captured 0-2 unsigned payload"


def vnsem_301_passive_parse_error(src: str) -> dict[str, Any]:
    """Return the Passive ES.SetMode reply reported in issue #104."""
    return {
        "id": 0,
        "src": src,
        "error": {
            "code": -32700,
            "message": "Parse error",
            "data": _VNSEM_REPORTED_PASSIVE_PARSE_DATA,
        },
    }


def uses_broadcast_reply_rules(profile: FirmwareProfile) -> bool:
    """Return whether the firmware follows the Venus E mini UDP rules.

    Captured on ``VNSEM-0`` 301 (issue #86):

    - it answers only a request whose source port is its API port;
    - it sends every reply to ``<subnet broadcast>:<API port>``;
    - it ignores a ``Marstek.GetDevice`` sent to the broadcast address, so
      LAN discovery never finds it and it must be added by IP.
    """
    return profile.family is DeviceFamily.VENUS_E_MINI
