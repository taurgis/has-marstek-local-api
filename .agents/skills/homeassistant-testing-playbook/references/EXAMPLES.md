# Test Examples (Home Assistant)

Concrete snippets for common cases. Adapt names/paths to your integration.

## conftest.py core
```python
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from custom_components.marstek.const import DOMAIN

@pytest.fixture
def mock_config_entry():
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"})
    return entry

@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(HomeAssistantSnapshotExtension)

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
```

## test_config_flow.py (happy path + errors)
```python
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN

async def test_user_flow_success(hass, aioclient_mocker):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM

    with aioclient_mocker.patch("pymarstek.MarstekUDPClient.discover_devices", return_value=[{
        "ip": "1.2.3.4",
        "ble_mac": "AA:BB:CC:DD:EE:FF",
        "mac": "AA:BB:CC:DD:EE:FF",
        "device_type": "Venus",
        "version": 3,
        "wifi_name": "marstek",
        "wifi_mac": "11:22:33:44:55:66",
        "model": "Venus",
        "firmware": "3.0"
    }]):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={"device": "0"})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == "1.2.3.4"

async def test_user_flow_no_devices(hass, aioclient_mocker):
    with aioclient_mocker.patch("pymarstek.MarstekUDPClient.discover_devices", return_value=[]):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices_found"}

async def test_dhcp_updates_ip(hass, mock_config_entry):
    mock_config_entry.add_to_hass(hass)
    discovery_info = type("dhcp", (), {"ip": "1.2.3.5", "hostname": "marstek", "macaddress": "AA:BB:CC:DD:EE:FF"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "dhcp"}, data=discovery_info)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN)[0].data["host"] == "1.2.3.5"
```

## test_init.py (setup/unload)
```python
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN

async def test_setup_unload(hass, aioclient_mocker):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"})
    entry.add_to_hass(hass)

    aioclient_mocker.patch("pymarstek.MarstekUDPClient.async_setup", return_value=None)
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.send_request", return_value={"result": {}})

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.NOT_LOADED
```

## test_sensor.py (coordinator + entity availability)
```python
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN

async def test_coordinator_success(hass, aioclient_mocker):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"})
    entry.add_to_hass(hass)

    aioclient_mocker.patch("pymarstek.MarstekUDPClient.async_setup", return_value=None)
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.send_request", return_value={"result": {}})
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.get_device_status", return_value={
        "device_mode": "SelfUse",
        "battery_soc": 55,
        "battery_power": 120,
        "pv1_power": 300,
    })

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.marstek_battery_level")
    assert state.state == "55"

async def test_coordinator_failure_marks_unavailable(hass, aioclient_mocker):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"})
    entry.add_to_hass(hass)

    aioclient_mocker.patch("pymarstek.MarstekUDPClient.async_setup", return_value=None)
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.send_request", return_value={"result": {}})
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.get_device_status", side_effect=TimeoutError)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.marstek_battery_level")
    assert state.state == "unavailable"
```

## Diagnostics snapshot
```python
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marstek.const import DOMAIN

async def test_diagnostics_snapshot(hass, hass_client, snapshot, aioclient_mocker):
    entry = MockConfigEntry(domain=DOMAIN, data={"host": "1.2.3.4", "ble_mac": "AA:BB:CC:DD:EE:FF"})
    entry.add_to_hass(hass)

    aioclient_mocker.patch("pymarstek.MarstekUDPClient.async_setup", return_value=None)
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.send_request", return_value={"result": {}})
    aioclient_mocker.patch("pymarstek.MarstekUDPClient.get_device_status", return_value={"device_mode": "SelfUse", "battery_soc": 55})

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, "AA:BB:CC:DD:EE:FF")})

    diag = {
        "entry": entry.as_dict(),
        "device": device.as_dict() if device else {},
    }
    assert diag == snapshot
```
