"""Register the battery health cards and the optional sidebar dashboard.

Static files use ``StaticPathConfig``. ``register_static_path`` was removed
in Home Assistant 2025.7:
https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/

``frontend.add_extra_js_url`` attaches the module to every frontend session,
so the card picker works without a Lovelace resource. The module defines its
elements on ``window.customElements`` only after ``<home-assistant>`` exists,
because Home Assistant swaps in a scoped registry when that element is
defined. Defining the strategy element earlier leaves it on the previous
registry, and the dashboard then times out waiting for
``ll-strategy-dashboard-marstek-battery-health``. The sidebar entry is a storage
dashboard with strategy ``custom:marstek-battery-health``, the same shape Home
Assistant uses when it creates the map dashboard. The strategy element is
``ll-strategy-dashboard-marstek-battery-health``:
https://developers.home-assistant.io/docs/frontend/custom-ui/custom-strategy/
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util.hass_dict import HassKey

from ..const import (
    CONF_SHOW_CAPACITY_DASHBOARD,
    DEFAULT_SHOW_CAPACITY_DASHBOARD,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_FRONTEND_URL: HassKey[str] = HassKey(f"{DOMAIN}_capacity_frontend_url")
_STATIC_PREFIX = "/marstek/capacity"
_MODULE_NAME = "marstek-battery-health.js"
_DASHBOARD_URL = "marstek-battery-health"
_STRATEGY = "custom:marstek-battery-health"
_DASHBOARD_ICON = "mdi:battery-heart-variant"

_ACTIVE_STATES = frozenset(
    {
        ConfigEntryState.LOADED,
        ConfigEntryState.SETUP_IN_PROGRESS,
        ConfigEntryState.SETUP_RETRY,
    }
)


def _module_path() -> Path:
    """Return the bundled card module."""
    return Path(__file__).resolve().parent.parent / "frontend" / _MODULE_NAME


def _version_token(payload: bytes) -> str:
    """Return a short content hash used as a cache buster."""
    return hashlib.sha256(payload).hexdigest()[:12]


async def async_register_capacity_frontend(hass: HomeAssistant) -> None:
    """Serve the card module and load it in the frontend, once per process."""
    if hass.data.get(_FRONTEND_URL):
        return
    http = getattr(hass, "http", None)
    if http is None or not hasattr(http, "async_register_static_paths"):
        return
    # Wait until the frontend data bucket exists. Registering the file
    # without a module URL would leave the card unloaded, and a second call
    # would add the static route twice.
    if hass.data.get(DATA_EXTRA_MODULE_URL) is None:
        _LOGGER.debug("Frontend is not loaded; skipping the battery health cards")
        return
    module = _module_path()
    if not module.is_file():
        _LOGGER.warning("Marstek battery health module is missing at %s", module)
        return
    payload = await hass.async_add_executor_job(module.read_bytes)
    directory = str(module.parent)
    await http.async_register_static_paths([StaticPathConfig(_STATIC_PREFIX, directory, True)])
    url = f"{_STATIC_PREFIX}/{_MODULE_NAME}?v={_version_token(payload)}"
    add_extra_js_url(hass, url)
    hass.data[_FRONTEND_URL] = url
    _LOGGER.debug("Registered Marstek battery health module at %s", url)


def _dashboard_wanted(hass: HomeAssistant, *, ignore_entry_id: str | None) -> bool:
    """Return whether any active entry asked for the sidebar dashboard."""
    return any(
        entry.entry_id != ignore_entry_id
        and entry.state in _ACTIVE_STATES
        and bool(
            entry.options.get(
                CONF_SHOW_CAPACITY_DASHBOARD,
                DEFAULT_SHOW_CAPACITY_DASHBOARD,
            )
        )
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def _dashboard_title(hass: HomeAssistant) -> str:
    """Return the sidebar title in the configured language, English as fallback."""
    translations = await async_get_translations(
        hass, hass.config.language or "en", "common", {DOMAIN}
    )
    return translations.get(f"component.{DOMAIN}.common.dashboard_title", "Marstek")


def _dashboards_collection(hass: HomeAssistant) -> Any | None:
    """Return the live Lovelace dashboard collection, when Lovelace is up.

    Lovelace exposes no public handle for its dashboards collection. The
    websocket list command is a bound method of the collection's websocket
    wrapper, so its ``__self__`` leads to the same collection the UI edits.
    Every step is checked, so a Home Assistant change makes this return None
    and the integration skips the sidebar entry instead of failing setup.
    """
    websocket = hass.data.get("websocket_api")
    if not isinstance(websocket, dict):
        return None
    registered = websocket.get("lovelace/dashboards/list")
    if not isinstance(registered, tuple) or not registered:
        return None
    owner = getattr(registered[0], "__self__", None)
    collection = getattr(owner, "storage_collection", None)
    if collection is None or not hasattr(collection, "async_items"):
        return None
    return collection


def _find_dashboard(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the dashboard item at this integration's URL path, if any."""
    for item in items:
        if item.get("url_path") == _DASHBOARD_URL:
            return item
    return None


def _dashboard_store(hass: HomeAssistant) -> Any | None:
    """Return the Lovelace config store behind the dashboard, when it exists."""
    lovelace = hass.data.get(LOVELACE_DATA)
    dashboards = getattr(lovelace, "dashboards", None)
    if not isinstance(dashboards, dict):
        return None
    store = dashboards.get(_DASHBOARD_URL)
    if store is None or not hasattr(store, "async_save"):
        return None
    return store


async def _saved_config(store: Any) -> Any:
    """Return the dashboard's saved config, or None before anything is saved."""
    try:
        return await store.async_load(False)
    except ConfigNotFound:
        return None


def _is_ours(config: Any) -> bool:
    """Return whether a saved config is empty or uses this integration's strategy.

    A dashboard someone else saved at the same URL path keeps its views and
    its sidebar setting.
    """
    if config is None:
        return True
    strategy = config.get("strategy") if isinstance(config, dict) else None
    return isinstance(strategy, dict) and strategy.get("type") == _STRATEGY


async def async_sync_capacity_dashboard(
    hass: HomeAssistant,
    *,
    ignore_entry_id: str | None = None,
) -> None:
    """Show or hide the sidebar dashboard to match config-entry options.

    A missing Lovelace collection is ignored. Device setup must not fail
    because the dashboard panel is unavailable.
    """
    collection = _dashboards_collection(hass)
    if collection is None:
        return
    wanted = _dashboard_wanted(hass, ignore_entry_id=ignore_entry_id)
    try:
        await _sync_dashboard(hass, collection, wanted=wanted)
    except (HomeAssistantError, vol.Invalid):
        _LOGGER.warning("Could not update the Marstek battery health dashboard", exc_info=True)


async def _sync_dashboard(hass: HomeAssistant, collection: Any, *, wanted: bool) -> None:
    """Create the dashboard, or set its sidebar entry, when it is this integration's."""
    existing = _find_dashboard(list(collection.async_items()))
    if existing is None:
        if not wanted:
            return
        await collection.async_create_item(
            {
                "url_path": _DASHBOARD_URL,
                "title": await _dashboard_title(hass),
                "icon": _DASHBOARD_ICON,
                "show_in_sidebar": True,
                "require_admin": False,
            }
        )
    store = _dashboard_store(hass)
    if store is None:
        return
    config = await _saved_config(store)
    if not _is_ours(config):
        _LOGGER.debug("Dashboard /%s holds another config; leaving it alone", _DASHBOARD_URL)
        return
    if existing is not None and bool(existing.get("show_in_sidebar", True)) != wanted:
        await collection.async_update_item(existing["id"], {"show_in_sidebar": wanted})
    if wanted and config is None:
        await store.async_save({"strategy": {"type": _STRATEGY}})
