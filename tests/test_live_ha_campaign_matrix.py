"""Compose inventory used by the live Home Assistant campaign."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "homeassistant-chrome-ui-testing"
    / "scripts"
)


def _load_campaign() -> object:
    """Load ha_live_campaign.py without requiring it to be a package."""
    path = _SCRIPTS / "ha_live_campaign.py"
    spec = importlib.util.spec_from_file_location("ha_live_campaign", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ha_live_campaign"] = module
    spec.loader.exec_module(module)
    return module


def test_compose_mocks_cover_supported_and_rejected_devices() -> None:
    """Every docker-compose mock is classified for live HA add vs reject."""
    campaign = _load_campaign()
    mocks = campaign.load_compose_mocks()
    hosts = {mock.host for mock in mocks}
    assert "172.28.0.20" in hosts
    assert "172.28.0.46" in hosts
    assert len(mocks) >= 26

    by_host = {mock.host: mock for mock in mocks}
    assert by_host["172.28.0.29"].expectation == "reject_unsupported"
    assert by_host["172.28.0.44"].expectation == "reject_unsupported"
    assert by_host["172.28.0.45"].expectation == "reject_unsupported"
    assert by_host["172.28.0.36"].expectation == "add_unknown"
    assert by_host["172.28.0.36"].family == "Unknown"
    assert by_host["172.28.0.20"].expectation == "add_supported"
    assert by_host["172.28.0.25"].supports_sys is True
    assert by_host["172.28.0.25"].supports_ups is True
    assert by_host["172.28.0.20"].supports_sys is False
    assert by_host["172.28.0.22"].supports_pv is True
    assert by_host["172.28.0.26"].supports_em_status is False
    assert by_host["172.28.0.42"].supports_em_status is True
    assert by_host["172.28.0.28"].supports_sys is True
    assert by_host["172.28.0.28"].supports_ups is False
    assert by_host["172.28.0.28"].max_manual_schedule_slot == 5
    assert by_host["172.28.0.22"].unique_port is True
    assert by_host["172.28.0.20"].unique_port is False
    assert by_host["172.28.0.20"].ble_mac == campaign.DEFAULT_MOCK_BLE_MAC
    assert campaign.campaign_mac(by_host["172.28.0.20"].ble_mac) == "00:9b:08:a5:aa:39"
    assert campaign.campaign_mac(by_host["172.28.0.25"].ble_mac) == "02:de:ad:be:ef:02"


def test_setup_expectation_matches_firmware_profile() -> None:
    """Unsupported HMG-50 VenusE is rejected; Venus E Pro stays unknown."""
    campaign = _load_campaign()
    assert campaign.setup_expectation("VenusE", 153) == "reject_unsupported"
    assert campaign.setup_expectation("VenusE", 156) == "reject_unsupported"
    assert campaign.setup_expectation("VenusE Pro", 1508) == "add_unknown"
    assert campaign.setup_expectation("VenusE 3.0", 150) == "add_supported"
    assert campaign.setup_expectation("VenusC", 153) == "add_supported"
    assert campaign.setup_expectation("Venus E mini", 145) == "add_supported"


def test_entity_by_key_matches_mac_unique_id_suffix() -> None:
    """Campaign looks up entities by BLE-MAC unique_id suffix, not slug."""
    campaign = _load_campaign()
    rows = [
        {"unique_id": "02:de:ad:be:ef:02_battery_soc", "entity_id": "sensor.one"},
        {
            "unique_id": "02:de:ad:be:ef:02_operating_mode",
            "entity_id": "select.one",
        },
    ]
    soc = campaign.entity_by_key(rows, "battery_soc")
    mode = campaign.entity_by_key(rows, "operating_mode")
    missing = campaign.entity_by_key(rows, "pv1_power")
    assert soc is not None and soc["entity_id"] == "sensor.one"
    assert mode is not None and mode["entity_id"] == "select.one"
    assert missing is None


def test_resolve_entry_host_uses_ble_mac_when_get_single_omits_data() -> None:
    """HA 2026 get_single wraps config_entry and omits data.host."""
    campaign = _load_campaign()
    mocks = campaign.load_compose_mocks()
    by_host = {mock.host: mock for mock in mocks}
    venus_e = by_host["172.28.0.25"]
    by_mac = campaign.mocks_by_mac(mocks)
    host = campaign.resolve_entry_host(
        row={
            "entry_id": "01TESTENTRY",
            "mac": "02:de:ad:be:ef:02",
            "unique_id": "02:de:ad:be:ef:02",
        },
        entry={"entry_id": "01TESTENTRY", "state": "loaded", "title": "Venus E"},
        mocks=by_mac,
        remembered={},
    )
    assert host == venus_e.host
    remembered = campaign.resolve_entry_host(
        row={"entry_id": "01TESTENTRY"},
        entry={},
        mocks=by_mac,
        remembered={"01TESTENTRY": "172.28.0.22"},
    )
    assert remembered == "172.28.0.22"
    wrapped = campaign.unwrap_config_entry(
        {"config_entry": {"entry_id": "abc", "state": "loaded"}}
    )
    assert wrapped["entry_id"] == "abc"
    assert campaign.campaign_mac("02deadbeef02") == "02:de:ad:be:ef:02"
    assert campaign.campaign_mac("not-a-mac") is None
    wrapped = {
        "home_assistant": {},
        "data": {"firmware_profile": {"family": "Venus E"}},
    }
    assert campaign.diagnostics_has_profile(wrapped) is True
    assert campaign.diagnostics_has_profile({"firmware_profile": {}}) is True
    assert campaign.diagnostics_has_profile({"ok": False}) is False


def test_analyze_ha_logs_counts_methods_and_pooled_getdevice() -> None:
    """Debug-log analysis extracts method frequency and reuseport collision cues."""
    campaign = _load_campaign()
    sample = "\n".join(
        [
            "2026-09-19 14:00:00.000 DEBUG (MainThread) "
            "[custom_components.marstek.pymarstek.udp] "
            'Send: 172.28.0.20:30000 | {"id": 1, "method": "ES.GetStatus"}',
            "2026-09-19 14:00:05.100 DEBUG (MainThread) "
            "[custom_components.marstek.pymarstek.udp] "
            'Recv: 172.28.0.20:30000 | {"id": 1}',
            "2026-09-19 14:00:05.200 DEBUG (MainThread) "
            "[custom_components.marstek.discovery] "
            "Querying device info from 172.28.0.25:30000 via pooled UDP client",
            "2026-09-19 14:00:06.000 WARNING (MainThread) "
            "[custom_components.marstek.pymarstek.udp] "
            "Request timeout: 172.28.0.22:30001",
        ]
    )
    analysis = campaign.analyze_ha_logs(sample)
    assert analysis["send_count"] == 1
    assert analysis["recv_count"] == 1
    assert analysis["methods"]["ES.GetStatus"] == 1
    assert analysis["getdevice_pooled"] == ["172.28.0.25:30000"]
    assert analysis["timeout_count"] == 1
    assert analysis["invalid_response"] == []
