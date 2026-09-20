"""Config-flow branches the happy-path suites do not reach.

The bronze `config-flow-test-coverage` rule asks for full coverage of the
config flow, so every guard in config_flow.py gets a test here rather than
only the paths a working device walks.
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.config_flow import MarstekConfigFlow
from custom_components.marstek.const import DOMAIN
from tests.conftest import patch_discovery, patch_manual_connection

from ._helpers import _pooled_udp_client


def _dhcp_info(*, ip: str | None, macaddress: str | None) -> Any:
    """Build a DHCP discovery payload with the two fields the step reads."""
    return type(
        "DhcpInfo",
        (),
        {"ip": ip, "hostname": "marstek", "macaddress": macaddress},
    )


def _device(**overrides: Any) -> dict[str, Any]:
    """Build a discovered-device mapping with a valid identity."""
    device: dict[str, Any] = {
        "ip": "1.2.3.4",
        "port": 30000,
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": 3,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
    }
    device.update(overrides)
    return device


async def test_user_step_discovery_succeeds_on_retry(hass: HomeAssistant) -> None:
    """An empty first sweep is retried, and the second sweep's devices are offered."""
    discover = AsyncMock(side_effect=[[], [_device()]])
    with (
        patch("custom_components.marstek.config_flow.discover_devices", discover),
        patch("custom_components.marstek.config_flow.asyncio.sleep", AsyncMock()),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert discover.await_count == 2


async def test_manual_step_without_a_reply_shows_cannot_connect(hass: HomeAssistant) -> None:
    """A manual host that answers nothing re-shows the form with cannot_connect."""
    _pooled_udp_client(hass)
    with patch_discovery([]), patch_manual_connection(None):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["step_id"] == "manual"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.4", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_dhcp_without_identity_aborts(hass: HomeAssistant) -> None:
    """DHCP discovery with no MAC carries no stable identity, so it aborts."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "dhcp"},
        data=_dhcp_info(ip="1.2.3.4", macaddress=None),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_integration_discovery_ignores_an_unusable_port(hass: HomeAssistant) -> None:
    """A non-numeric discovered port is dropped rather than failing discovery."""
    _pooled_udp_client(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "integration_discovery"},
        data=_device(port="not-a-port"),
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["flow_id"] == result["flow_id"]
    )
    assert flow is not None


async def test_confirm_step_without_identity_shows_error(hass: HomeAssistant) -> None:
    """A confirmed device that answers without a MAC cannot be adopted."""
    _pooled_udp_client(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=_device()
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection({"device_type": "Venus", "version": 3}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.4", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "invalid_discovery_info"}


async def test_confirm_step_reports_an_unparsable_reply(hass: HomeAssistant) -> None:
    """A malformed reply during confirm is surfaced as invalid_discovery_info."""
    _pooled_udp_client(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=_device()
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection(error=ValueError("garbage on the wire")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.4", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "invalid_discovery_info"}


async def test_discovery_skips_entries_for_other_devices(hass: HomeAssistant) -> None:
    """A discovery for one device leaves another device's entry untouched."""
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="99:88:77:66:55:44",
        data={
            CONF_HOST: "10.0.0.9",
            CONF_PORT: 30000,
            "ble_mac": "99:88:77:66:55:44",
            "mac": "99:88:77:66:55:44",
            "device_type": "Venus",
        },
    )
    other.add_to_hass(hass)
    _pooled_udp_client(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=_device()
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert other.data[CONF_HOST] == "10.0.0.9"


async def test_discovery_reloads_a_retrying_entry_with_nothing_to_update(
    hass: HomeAssistant,
) -> None:
    """A device answering again on its stored address ends the setup-retry wait."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="aa:bb:cc:dd:ee:ff",
        data={
            CONF_HOST: "1.2.3.4",
            CONF_PORT: 30000,
            "ble_mac": "AA:BB:CC:DD:EE:FF",
            "mac": "AA:BB:CC:DD:EE:FF",
            "device_type": "Venus",
            "version": 3,
            "wifi_name": "marstek",
            "wifi_mac": "11:22:33:44:55:66",
        },
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)

    with patch.object(hass.config_entries, "async_schedule_reload") as schedule_reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "integration_discovery"}, data=_device()
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    schedule_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_rejects_a_device_without_identity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfiguring onto a host that answers without a MAC is refused."""
    mock_config_entry.add_to_hass(hass)
    _pooled_udp_client(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    with patch_manual_connection({"device_type": "Venus", "version": 3}):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.9", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"] == {"base": "invalid_discovery_info"}
    assert mock_config_entry.data[CONF_HOST] == "1.2.3.4"


async def test_reconfigure_rejects_a_different_device(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfiguring onto another Marstek device is refused, not silently adopted."""
    mock_config_entry.add_to_hass(hass)
    _pooled_udp_client(hass)

    result = await mock_config_entry.start_reconfigure_flow(hass)
    other_device = _device(
        ble_mac="99:88:77:66:55:44",
        mac="99:88:77:66:55:44",
        wifi_mac="99:88:77:66:55:45",
    )
    with patch_manual_connection(other_device):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.9", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure_confirm"
    assert result["errors"] == {"base": "unique_id_mismatch"}
    assert mock_config_entry.data[CONF_HOST] == "1.2.3.4"


async def test_discovery_handler_aborts_without_a_discovered_address(
    hass: HomeAssistant,
) -> None:
    """The shared discovery handler refuses a flow that never learned an address.

    Both callers check this first, so the guard is only reachable directly;
    it stays because the handler is what any future discovery source will
    call.
    """
    flow = MarstekConfigFlow()
    flow.hass = hass

    result = await flow._async_handle_discovery_with_unique_id()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_confirm_step_without_a_discovered_address_falls_back_to_manual(
    hass: HomeAssistant,
) -> None:
    """Confirming a flow that has no address left drops back to manual entry."""
    flow = MarstekConfigFlow()
    flow.hass = hass

    result = await flow.async_step_confirm()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "invalid_discovery_info"}


async def test_confirm_step_rejects_an_unsupported_device(hass: HomeAssistant) -> None:
    """A confirmed host that answers as a Venus E2.0 is refused."""
    _pooled_udp_client(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=_device()
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection(_device(device_type="Venus E2.0")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.4", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "unsupported_device"}


async def test_confirm_step_reports_a_socket_failure(hass: HomeAssistant) -> None:
    """A socket error while confirming is surfaced as cannot_connect."""
    _pooled_udp_client(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "integration_discovery"}, data=_device()
    )
    assert result["step_id"] == "confirm"

    with patch_manual_connection(error=OSError("network unreachable")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_HOST: "1.2.3.4", CONF_PORT: 30000}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["errors"] == {"base": "cannot_connect"}
