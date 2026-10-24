"""Capacity card registration and the optional sidebar dashboard."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, UrlManager
from homeassistant.components.lovelace.const import LOVELACE_DATA, ConfigNotFound
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN
from custom_components.marstek.helpers.capacity_frontend import (
    async_register_capacity_frontend,
    async_sync_capacity_dashboard,
)


class _Store:
    """Stand-in for a Lovelace storage dashboard."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config
        self.saved: dict[str, Any] | None = None

    async def async_load(self, force: bool) -> dict[str, Any]:
        """Return the saved config, or the same error Lovelace uses when empty."""
        del force
        if self.config is None:
            raise ConfigNotFound
        return self.config

    async def async_save(self, config: dict[str, Any]) -> None:
        """Remember the strategy config the integration writes."""
        self.config = config
        self.saved = config


class _Collection:
    """Stand-in for the Lovelace dashboards collection."""

    def __init__(self, hass: HomeAssistant, store: _Store) -> None:
        self.hass = hass
        self.store = store
        self.items: dict[str, dict[str, Any]] = {}
        self.fail = False

    def async_items(self) -> list[dict[str, Any]]:
        """Return the dashboards currently stored."""
        return list(self.items.values())

    async def async_create_item(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a dashboard and attach the store, as Lovelace's listener does."""
        if self.fail:
            raise RuntimeError("lovelace unavailable")
        item = {"id": "marstek-capacity", **data}
        self.items[item["id"]] = item
        self.hass.data[LOVELACE_DATA].dashboards[data["url_path"]] = self.store
        return item

    async def async_update_item(self, item_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Apply a sidebar visibility change."""
        self.items[item_id].update(updates)
        return self.items[item_id]


class _DashboardApi:
    """Bound-method owner the integration uses to find the live collection."""

    def __init__(self, storage_collection: _Collection) -> None:
        self.storage_collection = storage_collection

    def ws_list_item(self, hass: HomeAssistant, connection: object, msg: dict[str, Any]) -> None:
        """Unused. The integration only needs this method's bound owner."""
        del hass, connection, msg


def _install_lovelace(hass: HomeAssistant, collection: _Collection) -> None:
    """Point the websocket command table at the fake collection."""
    api = _DashboardApi(collection)
    hass.data["websocket_api"] = {"lovelace/dashboards/list": (api.ws_list_item, None)}
    hass.data[LOVELACE_DATA] = SimpleNamespace(dashboards={})


def _entry(hass: HomeAssistant, *, show: bool) -> MockConfigEntry:
    """Add a loaded config entry with the dashboard option set."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "127.0.0.1"},
        options={"show_capacity_dashboard": show},
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def _enable_frontend(hass: HomeAssistant) -> MagicMock:
    """Give the registrar an HTTP server and a frontend module bucket."""
    http = MagicMock()
    http.async_register_static_paths = AsyncMock()
    hass.http = http
    hass.data[DATA_EXTRA_MODULE_URL] = UrlManager(lambda _action, _url: None, [])
    return http


async def test_register_skips_until_http_and_frontend_exist(hass: HomeAssistant) -> None:
    """A test hass without the frontend does not register a static route."""
    hass.http = None
    await async_register_capacity_frontend(hass)

    http = MagicMock()
    http.async_register_static_paths = AsyncMock()
    hass.http = http
    await async_register_capacity_frontend(hass)
    http.async_register_static_paths.assert_not_called()


async def test_register_loads_the_module_once(hass: HomeAssistant) -> None:
    """The card URL carries a content hash and is not added twice."""
    http = _enable_frontend(hass)
    await async_register_capacity_frontend(hass)
    await async_register_capacity_frontend(hass)

    http.async_register_static_paths.assert_awaited_once()
    urls = hass.data[DATA_EXTRA_MODULE_URL].urls
    assert len(urls) == 1
    url = next(iter(urls))
    assert url.startswith("/marstek/capacity/marstek-capacity-card.js?v=")
    assert len(url.rsplit("v=", 1)[1]) == 12


async def test_register_warns_when_the_module_is_missing(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A partial install logs and leaves the frontend untouched."""
    http = _enable_frontend(hass)
    missing = Path("/tmp/marstek-capacity-missing.js")
    with patch(
        "custom_components.marstek.helpers.capacity_frontend._module_path",
        return_value=missing,
    ):
        await async_register_capacity_frontend(hass)

    http.async_register_static_paths.assert_not_called()
    assert "missing" in caplog.text.lower()


async def test_dashboard_is_created_for_a_dutch_install(hass: HomeAssistant) -> None:
    """Enabling the option adds a sidebar dashboard and a strategy config."""
    hass.config.language = "nl"
    _entry(hass, show=True)
    store = _Store()
    collection = _Collection(hass, store)
    _install_lovelace(hass, collection)

    await async_sync_capacity_dashboard(hass)

    item = collection.async_items()[0]
    assert item["url_path"] == "marstek-capacity"
    assert item["title"] == "Batterijcapaciteit"
    assert item["show_in_sidebar"] is True
    assert item["icon"] == "mdi:battery-heart-variant"
    assert store.saved == {"strategy": {"type": "custom:marstek-capacity"}}

    store.saved = None
    await async_sync_capacity_dashboard(hass)
    assert store.saved is None
    assert len(collection.async_items()) == 1


async def test_dashboard_hides_without_deleting_a_custom_strategy(hass: HomeAssistant) -> None:
    """Turning the option off keeps the dashboard and only removes the sidebar."""
    hass.config.language = "en"
    entry = _entry(hass, show=False)
    store = _Store({"strategy": {"type": "custom:marstek-capacity"}, "views": []})
    collection = _Collection(hass, store)
    collection.items["marstek-capacity"] = {
        "id": "marstek-capacity",
        "url_path": "marstek-capacity",
        "title": "Battery Capacity",
        "show_in_sidebar": True,
    }
    _install_lovelace(hass, collection)
    hass.data[LOVELACE_DATA].dashboards["marstek-capacity"] = store

    await async_sync_capacity_dashboard(hass)

    assert collection.items["marstek-capacity"]["show_in_sidebar"] is False
    assert store.saved is None

    hass.config_entries.async_update_entry(entry, options={"show_capacity_dashboard": True})
    await async_sync_capacity_dashboard(hass)
    assert collection.items["marstek-capacity"]["show_in_sidebar"] is True

    collection.items.clear()
    hass.config.language = "en"
    await async_sync_capacity_dashboard(hass)
    assert collection.async_items()[0]["title"] == "Battery Capacity"


async def test_dashboard_ignores_a_missing_collection_and_a_create_failure(
    hass: HomeAssistant,
) -> None:
    """Lovelace trouble must not raise into config-entry setup."""
    await async_sync_capacity_dashboard(hass)

    _entry(hass, show=True)
    store = _Store()
    collection = _Collection(hass, store)
    collection.fail = True
    _install_lovelace(hass, collection)
    await async_sync_capacity_dashboard(hass)
    assert collection.async_items() == []

    collection.fail = False
    await async_sync_capacity_dashboard(hass, ignore_entry_id=_entry_id(hass))
    assert collection.async_items() == []


def _entry_id(hass: HomeAssistant) -> str:
    """Return the only Marstek entry id."""
    return hass.config_entries.async_entries(DOMAIN)[0].entry_id
