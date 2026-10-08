"""Control-firmware wire quirks used only by the mock device.

Home Assistant capability gates stay in ``firmware_profile.py``. These helpers
encode recv-list and field-table evidence from archived Control blobs so the
mock answers the way those images dispatch Open API methods.
"""

from __future__ import annotations

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
# ``VNSA-0``. VNSA-0 150 still has ``VenusA-%s``. VNSA-0 1508 banners
# VEPRO-0 / VenusE Pro (unknown family); its src site is ``%s-%s`` next to
# the product name ``VenusE Pro``, not the SKU ``VNSA-0``.
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


def uses_broadcast_reply_rules(profile: FirmwareProfile) -> bool:
    """Return whether the firmware follows the Venus E mini UDP rules.

    Captured on ``VNSEM-0`` 301 (issue #86):

    - it answers only a request whose source port is its API port;
    - it sends every reply to ``<subnet broadcast>:<API port>``;
    - it ignores a ``Marstek.GetDevice`` sent to the broadcast address, so
      LAN discovery never finds it and it must be added by IP.
    """
    return profile.family is DeviceFamily.VENUS_E_MINI
