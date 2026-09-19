"""Control-firmware wire quirks used only by the mock device.

Home Assistant capability gates stay in ``firmware_profile.py``. These helpers
encode recv-list and field-table evidence from archived Control blobs so the
mock answers the way those images dispatch Open API methods.
"""

from __future__ import annotations

from custom_components.marstek.firmware_profile import DeviceFamily, FirmwareProfile


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

    Present from VNSA-0 **1487** and from generation **149** on VNSE3-0 /
    VNSA-0 / VNSD-0 (including VenusE Pro **1508**). HMG-50 never has them.
    Venus E mini is not in the Control catalog; do not invent the methods.
    """
    if profile.hmg50_control or profile.family is DeviceFamily.VENUS_E_MINI:
        return False
    version = profile.firmware_version
    if version is None:
        return False
    return version >= 149 or version == 1487


def pv_method_not_found_extra_data(profile: FirmwareProfile) -> int | None:
    """Return the captured PV.GetStatus ``error.data`` for non-PV firmware.

    Venus E 3.0 firmware **150** LAN capture is ``-32601`` with ``data: 424``.
    Use Control generation so ``ver=1476`` (app 147.6) does not inherit that
    150-only payload. Other non-PV families have no matching capture.
    """
    generation = profile.control_generation
    if (
        profile.family is DeviceFamily.VENUS_E
        and generation is not None
        and generation >= 150
    ):
        return 424
    return None
