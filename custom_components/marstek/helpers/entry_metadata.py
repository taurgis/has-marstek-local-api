"""Apply GetDevice metadata onto a config entry and the device registry.

Broadcast discovery, setup unicast, and the scanner's VLAN fallback all
learn ``ver`` the same way. One writer keeps ``entry.data``,
``sw_version``, and the firmware-profile reload decision consistent.

Config entry mutations go through ``async_update_entry``:
https://developers.home-assistant.io/docs/config_entries_index/
https://developers.home-assistant.io/blog/2024/02/12/async_update_entry/
Device firmware is ``sw_version`` in the device registry:
https://developers.home-assistant.io/docs/device_registry_index/
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from ..const import DEFAULT_UDP_PORT, DOMAIN
from ..discovery import get_device_info
from ..firmware_profile import resolve_firmware_profile_from_metadata
from ..pymarstek import MarstekUDPClient
from .device_lookup import async_lookup_device_by_identifier
from .domain_data import domain_data
from .flow_helpers import (
    get_unique_id_from_device_info,
    identities_overlap,
    identity_macs_from_entry,
    identity_macs_from_mapping,
    metadata_from_device_info,
)

_LOGGER = logging.getLogger(__name__)

_RUNTIME_UPDATE_STATES = frozenset({ConfigEntryState.LOADED})


def apply_entry_metadata_update(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dict[str, Any],
    *,
    reload_on_profile_change: bool = True,
) -> bool:
    """Store changed GetDevice fields on *entry* and the device registry.

    Returns True when ``entry.data`` changed. A capability or reset-safety
    change reloads a loaded entry so platforms recreate entities. Cosmetic
    fields (Wi-Fi name, a ``ver`` that does not change the profile) are
    written without a reload.
    """
    updates = _metadata_updates(entry, device)
    if not updates:
        return False

    old_profile = resolve_firmware_profile_from_metadata(entry.data)
    merged = {**entry.data, **updates}
    new_profile = resolve_firmware_profile_from_metadata(merged)
    profile_changed = old_profile.setup_reload_signature != new_profile.setup_reload_signature
    should_reload = profile_changed and reload_on_profile_change

    _LOGGER.debug(
        "Updating device metadata for %s: %s",
        entry.title,
        ", ".join(f"{key}={value}" for key, value in updates.items()),
    )
    if profile_changed:
        _LOGGER.info(
            "Firmware profile changed for %s; config entry %s",
            entry.title,
            "will reload" if should_reload else "updated in place during setup",
        )

    if not should_reload and entry.state in _RUNTIME_UPDATE_STATES:
        domain_data(hass).suppress_reloads.add(entry.entry_id)

    hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})

    if not should_reload and entry.state in _RUNTIME_UPDATE_STATES:
        _apply_runtime_metadata(entry, updates)

    _update_device_registry(hass, entry, updates)
    return True


def _metadata_updates(entry: ConfigEntry, device: dict[str, Any]) -> dict[str, Any]:
    """Return discovery fields that differ from the stored entry."""
    discovered = metadata_from_device_info(device)
    return {key: value for key, value in discovered.items() if entry.data.get(key) != value}


def _apply_runtime_metadata(entry: ConfigEntry, updates: dict[str, Any]) -> None:
    """Push cosmetic metadata into a loaded entry without reloading it."""
    runtime_data = getattr(entry, "runtime_data", None)
    device_info = getattr(runtime_data, "device_info", None)
    if isinstance(device_info, dict):
        device_info.update(updates)
    coordinator = getattr(runtime_data, "coordinator", None)
    set_updated = getattr(coordinator, "async_set_updated_data", None)
    if callable(set_updated):
        set_updated(getattr(coordinator, "data", None) or {})


def _update_device_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    updates: dict[str, Any],
) -> None:
    """Update device registry metadata when version/model changes."""
    device_identifier = get_unique_id_from_device_info(entry.data)
    if device_identifier is None:
        return

    device_registry = dr.async_get(hass)
    device = async_lookup_device_by_identifier(
        device_registry,
        (DOMAIN, device_identifier),
        config_entry_id=entry.entry_id,
    )
    if not device:
        return

    update_kwargs: dict[str, Any] = {}
    if "version" in updates:
        update_kwargs["sw_version"] = str(updates["version"])
    if "device_type" in updates:
        update_kwargs["model"] = updates["device_type"]

    if update_kwargs:
        device_registry.async_update_device(device.id, **update_kwargs)


async def async_refresh_entry_from_unicast(
    hass: HomeAssistant,
    entry: ConfigEntry,
    udp_client: MarstekUDPClient,
    *,
    reload_on_profile_change: bool = True,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Unicast ``Marstek.GetDevice`` and apply firmware metadata.

    Uses the pooled client so the request shares the per-IP throttle and
    device I/O lock. Failures are returned as a status dict and never
    raised, so setup and polling keep running on the stored version.
    """
    result: dict[str, Any] = {"entry_id": entry.entry_id, "status": "no_reply"}
    host = entry.data.get(CONF_HOST)
    if not isinstance(host, str) or not host:
        result["status"] = "missing_host"
        return result

    port = int(entry.data.get(CONF_PORT, DEFAULT_UDP_PORT))
    try:
        device = await get_device_info(
            host=host,
            port=port,
            timeout=timeout,
            udp_client=udp_client,
            bypass_rate_limit=False,
            quiet=True,
        )
    except Exception as err:
        _LOGGER.debug("Unicast GetDevice failed for %s: %s", entry.title, err)
        result["status"] = "error"
        return result

    if device is None:
        return result

    if not identities_overlap(
        identity_macs_from_entry(entry),
        identity_macs_from_mapping(device),
    ):
        result["status"] = "identity_mismatch"
        return result

    changed = apply_entry_metadata_update(
        hass,
        entry,
        device,
        reload_on_profile_change=reload_on_profile_change,
    )
    result["version"] = device.get("version")
    result["status"] = "updated" if changed else "unchanged"
    return result
