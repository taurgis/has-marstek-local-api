"""DHCP leases whose MAC is not the BLE MAC (issue #98)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_IGNORE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN

# Venus E 3.0 / firmware 150, as reported on issue #98.
_BLE_MAC = "00:9b:08:05:ea:92"
_DHCP_MAC = "00:9b:08:06:38:ee"
_WIFI_MAC = "dc:39:6f:4d:00:84"
_HOST = "192.168.10.45"


def _dhcp(ip: str = _HOST, mac: str = _DHCP_MAC) -> Any:
    """Build the DHCP payload Home Assistant passes to ``async_step_dhcp``."""
    return type(
        "DhcpInfo",
        (),
        {"ip": ip, "hostname": "marstekvenus3-0", "macaddress": mac},
    )()


def _entry(**overrides: Any) -> MockConfigEntry:
    """A manually added Venus E whose unique id is the Local API BLE MAC."""
    data: dict[str, Any] = {
        "host": _HOST,
        "port": 30000,
        "ble_mac": _BLE_MAC,
        "mac": _WIFI_MAC,
        "wifi_mac": _WIFI_MAC,
        "device_type": "VenusE 3.0",
        "version": 150,
    }
    data.update(overrides.pop("data", {}))
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=overrides.pop("unique_id", _BLE_MAC),
        source=overrides.pop("source", "user"),
        data=data,
        **overrides,
    )


def _device(**overrides: Any) -> dict[str, Any]:
    """Local API ``Marstek.GetDevice`` result for the reported battery."""
    device = {
        "ip": _HOST,
        "ble_mac": _BLE_MAC,
        "mac": _WIFI_MAC,
        "wifi_mac": _WIFI_MAC,
        "device_type": "VenusE 3.0",
        "version": 150,
        "wifi_name": "TP-LINK",
    }
    device.update(overrides)
    return device


def _progress_unique_id(hass: HomeAssistant, flow_id: str) -> str | None:
    """Return the unique id Home Assistant stored on an in-progress flow."""
    for flow in hass.config_entries.flow.async_progress():
        if flow["flow_id"] == flow_id:
            unique_id = flow["context"].get("unique_id")
            return unique_id if isinstance(unique_id, str) else None
    return None


async def test_dhcp_lease_matches_manually_added_ble_entry(hass: HomeAssistant) -> None:
    """A DHCP MAC that is neither BLE nor wifi_mac still is the added battery."""
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.unique_id == _BLE_MAC
    assert updated.data["host"] == _HOST
    assert updated.data["network_mac"] == _DHCP_MAC
    assert updated.data["port"] == 30000


async def test_dhcp_lease_keeps_custom_port_when_identity_differs(
    hass: HomeAssistant,
) -> None:
    """Resolving the BLE MAC must not rewrite a port the user configured."""
    entry = _entry(data={"port": 30002, "host": "10.0.0.8"})
    entry.add_to_hass(hass)

    async def _probe(host: str, port: int = 30000, **_kwargs: Any) -> dict[str, Any] | None:
        if port == 30002:
            return _device(ip="10.1.0.9")
        return None

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(side_effect=_probe),
    ) as probed:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp(ip="10.1.0.9")
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_entries(DOMAIN)[0]
    assert updated.data["host"] == "10.1.0.9"
    assert updated.data["port"] == 30002
    assert updated.data["network_mac"] == _DHCP_MAC
    assert probed.await_count >= 1


async def test_dhcp_does_not_attach_lease_to_another_battery(hass: HomeAssistant) -> None:
    """A second battery that shares the access-point BSSID stays on its own entry."""
    other = _entry(
        unique_id="00:11:22:33:44:55",
        data={
            "host": "192.168.10.80",
            "port": 30000,
            "ble_mac": "00:11:22:33:44:55",
            "mac": _WIFI_MAC,
            "wifi_mac": _WIFI_MAC,
            "device_type": "VenusE 3.0",
            "version": 150,
        },
    )
    other.add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert _progress_unique_id(hass, result["flow_id"]) == _BLE_MAC
    untouched = hass.config_entries.async_entries(DOMAIN)[0]
    assert untouched.data["host"] == "192.168.10.80"
    assert "network_mac" not in untouched.data


async def test_dhcp_confirm_uses_ble_identity_and_stores_network_mac(
    hass: HomeAssistant,
) -> None:
    """Confirming a resolved lease creates one entry keyed by the BLE MAC."""
    device = _device()
    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=device),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )
        assert result["type"] is FlowResultType.FORM
        assert _progress_unique_id(hass, result["flow_id"]) == _BLE_MAC
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": _HOST, "port": 30000},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == _BLE_MAC
    assert result["data"]["network_mac"] == _DHCP_MAC
    assert result["data"]["ble_mac"] == _BLE_MAC


async def test_dhcp_confirm_rejects_a_different_device(hass: HomeAssistant) -> None:
    """Pointing the confirm form at another battery is still a mismatch."""
    other = _device(
        ble_mac="00:11:22:33:44:55",
        mac="aa:bb:cc:dd:ee:ff",
        wifi_mac="aa:bb:cc:dd:ee:ff",
        ip="192.168.10.99",
    )

    async def _probe(host: str, port: int = 30000, **_kwargs: Any) -> dict[str, Any]:
        if host == "192.168.10.99":
            return other
        return _device()

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(side_effect=_probe),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )
        assert result["step_id"] == "confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={"host": "192.168.10.99", "port": 30000},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "unique_id_mismatch"


async def test_ignored_dhcp_mac_is_not_offered_again(hass: HomeAssistant) -> None:
    """An ignored lease stays ignored, including when no battery is configured."""
    MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_IGNORE,
        unique_id=_DHCP_MAC,
        title="Marstek",
        data={},
    ).add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device()),
    ) as probed:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp(ip="192.168.10.50")
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probed.assert_not_awaited()
    assert len(hass.config_entries.async_entries(DOMAIN, include_ignore=False)) == 0


async def test_ignored_lease_still_updates_the_configured_battery(
    hass: HomeAssistant,
) -> None:
    """Ignore suppresses the card and the configured entry still learns the new IP."""
    entry = _entry(data={"host": "192.168.10.40"})
    entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_IGNORE,
        unique_id=_DHCP_MAC,
        title="Marstek",
        data={},
    ).add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device(ip="192.168.10.45")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp(ip="192.168.10.45")
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated is not None
    assert updated.data["host"] == "192.168.10.45"
    assert updated.data["network_mac"] == _DHCP_MAC
    assert hass.config_entries.flow.async_progress() == []


async def test_ignored_ble_identity_suppresses_a_resolved_lease(hass: HomeAssistant) -> None:
    """Ignoring the resolved discovery stores the BLE MAC, and the next lease stops."""
    MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_IGNORE,
        unique_id=_BLE_MAC,
        title="Marstek",
        data={},
    ).add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_access_point_bssid_lease_does_not_move_the_battery(
    hass: HomeAssistant,
) -> None:
    """A lease for the stored wifi_mac is the access point, not the battery."""
    entry = _entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "dhcp"},
            data=_dhcp(ip="192.168.10.1", mac=_WIFI_MAC),
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    unchanged = hass.config_entries.async_entries(DOMAIN)[0]
    assert unchanged.data["host"] == _HOST
    assert "network_mac" not in unchanged.data


async def test_known_dhcp_mac_updates_without_another_probe(hass: HomeAssistant) -> None:
    """Once the lease MAC is stored, a later DHCP sighting does not query the device."""
    entry = _entry(data={"host": "192.168.10.40", "network_mac": _DHCP_MAC})
    entry.add_to_hass(hass)

    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device()),
    ) as probed:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp(ip="192.168.10.77")
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    probed.assert_not_awaited()
    updated = hass.config_entries.async_get_entry(entry.entry_id)
    assert updated is not None
    assert updated.data["host"] == "192.168.10.77"
    assert updated.data["port"] == 30000


async def test_dhcp_unsupported_device_aborts(hass: HomeAssistant) -> None:
    """Venus E 2.0 answering the lease is refused instead of offered for setup."""
    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value=_device(device_type="VenusE")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


async def test_dhcp_probe_skips_an_answer_without_a_mac(hass: HomeAssistant) -> None:
    """A GetDevice reply with no MAC is not a battery, so the lease stays unconfirmed."""
    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(return_value={"device_type": "VenusE 3.0", "ip": _HOST}),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert _progress_unique_id(hass, result["flow_id"]) == _DHCP_MAC


async def test_dhcp_probe_failure_still_offers_confirm(hass: HomeAssistant) -> None:
    """A battery that does not answer GetDevice can still be confirmed by hand."""
    with patch(
        "custom_components.marstek.config_flow.get_device_info",
        new=AsyncMock(side_effect=TimeoutError()),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "dhcp"}, data=_dhcp()
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert _progress_unique_id(hass, result["flow_id"]) == _DHCP_MAC


async def test_integration_discovery_honors_an_ignored_ble_mac(hass: HomeAssistant) -> None:
    """The scanner rediscovering an ignored BLE MAC does not open another card."""
    MockConfigEntry(
        domain=DOMAIN,
        source=SOURCE_IGNORE,
        unique_id=_BLE_MAC,
        title="Marstek",
        data={},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data=_device(),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
