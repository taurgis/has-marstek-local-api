"""Repair issues and entity cleanup driven by the firmware profile."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import (
    DOMAIN,
)
from tests.conftest import (
    create_mock_client,
    patch_marstek_integration,
)


async def test_repair_issue_created_on_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test connection repair issue is created on setup failure."""
    mock_config_entry.add_to_hass(hass)

    failing_client = create_mock_client(send_request_error=TimeoutError("timeout"))
    with patch_marstek_integration(client=failing_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    issue_registry = ir.async_get(hass)
    issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_repair_issue_cleared_on_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test connection repair issue is cleared on successful setup."""
    mock_config_entry.add_to_hass(hass)

    issue_registry = ir.async_get(hass)
    issue_id = f"cannot_connect_{mock_config_entry.entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="cannot_connect",
        translation_placeholders={"host": "1.2.3.4", "error": "timeout"},
        data={"entry_id": mock_config_entry.entry_id},
    )

    working_client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=working_client):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_openapi_reset_issue_created_for_legacy_firmware(
    hass: HomeAssistant,
) -> None:
    """Venus E Control below 150 raises a non-fixable Open API reset warning."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusE 3.0",
            "version": 147,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        issue_registry = ir.async_get(hass)
        issue = issue_registry.async_get_issue(
            DOMAIN, f"openapi_reset_prone_{entry.entry_id}"
        )
        assert issue is not None
        assert issue.translation_key == "openapi_reset_prone"
        assert issue.severity is ir.IssueSeverity.WARNING
        assert issue.is_fixable is False
        assert issue.translation_placeholders == {
            "family": "Venus E",
            "firmware": "147",
        }

        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"openapi_reset_prone_{entry.entry_id}"
        )
        is None
    )


async def test_openapi_reset_issue_skipped_for_firmware_150(
    hass: HomeAssistant,
) -> None:
    """Venus E Control 150 does not warn; community report says issue #15 is fixed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusE 3.0",
            "version": 150,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    issue_registry = ir.async_get(hass)
    assert (
        issue_registry.async_get_issue(
            DOMAIN, f"openapi_reset_prone_{entry.entry_id}"
        )
        is None
    )
    client.set_openapi_reset_prone.assert_called_with(
        "1.2.3.4", False, owner=entry.entry_id
    )
    client.set_openapi_retransmit_safe.assert_called_with("1.2.3.4", True)


async def test_openapi_reset_issue_created_when_connection_fails(
    hass: HomeAssistant,
) -> None:
    """Firmware warning is created from metadata before the first UDP probe."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusE 3.0",
            "version": 147,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    client = create_mock_client(send_request_error=TimeoutError("timeout"))
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.SETUP_RETRY
    issue_registry = ir.async_get(hass)
    issue = issue_registry.async_get_issue(
        DOMAIN, f"openapi_reset_prone_{entry.entry_id}"
    )
    assert issue is not None
    assert issue.translation_key == "openapi_reset_prone"
    client.set_openapi_reset_prone.assert_called_with(
        "1.2.3.4", True, owner=entry.entry_id
    )
    client.set_openapi_retransmit_safe.assert_called_with("1.2.3.4", False)


async def test_reset_prone_setup_removes_bat_status_entities(
    hass: HomeAssistant,
) -> None:
    """Enabled Bat.GetStatus entities are dropped on reset-prone firmware."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusE 3.0",
            "version": 147,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    unique_id = "aa:bb:cc:dd:ee:ff_bat_temp"
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=unique_id,
        config_entry=entry,
    )

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None


async def test_non_meter_firmware_removes_em_status_entities(
    hass: HomeAssistant,
) -> None:
    """EM entities left by an older release are dropped, not left unavailable.

    The sensor platform stops adding them on firmware that is not an Open API
    meter client, so a registry entry that survives the upgrade would be
    restored as permanently unavailable.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusC",
            "version": 153,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    em_unique_id = "aa:bb:cc:dd:ee:ff_em_total_power"
    kept_unique_id = "aa:bb:cc:dd:ee:ff_battery_soc"
    for unique_id in (em_unique_id, kept_unique_id):
        entity_registry.async_get_or_create(
            domain="sensor",
            platform=DOMAIN,
            unique_id=unique_id,
            config_entry=entry,
        )

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, em_unique_id) is None
    assert (
        entity_registry.async_get_entity_id("sensor", DOMAIN, kept_unique_id)
        is not None
    )


async def test_meter_firmware_keeps_em_status_entities(
    hass: HomeAssistant,
) -> None:
    """Firmware that does answer EM.GetStatus keeps its meter entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            "host": "1.2.3.4",
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "VenusE 3.0",
            "version": 150,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)

    entity_registry = er.async_get(hass)
    em_unique_id = "aa:bb:cc:dd:ee:ff_em_total_power"
    entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=em_unique_id,
        config_entry=entry,
    )

    client = create_mock_client(
        status={"device_mode": "auto", "battery_soc": 50, "battery_power": 100}
    )
    with patch_marstek_integration(client=client):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert (
        entity_registry.async_get_entity_id("sensor", DOMAIN, em_unique_id) is not None
    )
