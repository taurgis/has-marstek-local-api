"""Zeros the firmware reports while its BMS is silent never reach the status."""

from __future__ import annotations

from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_es_mode_response,
    parse_es_status_response,
)


def _es_status(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {"id": 0, "pv_power": 0, "ongrid_power": 0, "offgrid_power": 0}
    return {"id": 1, "result": base | fields}


class TestEsStatus:
    """ES.GetStatus ``bat_cap`` 0 marks the battery fields as placeholders."""

    def test_zero_capacity_and_soc_are_dropped(self) -> None:
        parsed = parse_es_status_response(_es_status(bat_soc=0, bat_cap=0))

        assert parsed["bat_cap"] is None
        assert parsed["battery_soc"] is None

    def test_nonzero_soc_survives_zero_capacity(self) -> None:
        """Fields drop one at a time: Venus D 150 sent bat_soc 76 with bat_cap 0."""
        parsed = parse_es_status_response(_es_status(bat_soc=76, bat_cap=0))

        assert parsed["bat_cap"] is None
        assert parsed["battery_soc"] == 76

    def test_drained_pack_reports_zero_soc(self) -> None:
        parsed = parse_es_status_response(_es_status(bat_soc=0, bat_cap=5120))

        assert parsed["battery_soc"] == 0
        assert parsed["bat_cap"] == 5120


class TestBatStatus:
    """Bat.GetStatus ``rated_capacity`` 0 marks the pack fields as placeholders."""

    def test_silent_bms_reply_keeps_only_flags(self) -> None:
        parsed = parse_bat_status_response(
            {
                "id": 1,
                "result": {
                    "id": 0,
                    "soc": 0,
                    "charg_flag": False,
                    "dischrg_flag": False,
                    "bat_temp": 0.0,
                    "bat_capacity": 0,
                    "rated_capacity": 0,
                },
            }
        )

        assert parsed == {
            "bat_temp": None,
            "bat_charg_flag": False,
            "bat_dischrg_flag": False,
            "bat_capacity": None,
            "bat_rated_capacity": None,
            "bat_soc_detailed": None,
        }

    def test_real_readings_survive_zero_rated_capacity(self) -> None:
        parsed = parse_bat_status_response(
            {"id": 1, "result": {"soc": 76, "bat_temp": 21.5, "rated_capacity": 0}}
        )

        assert parsed["bat_soc_detailed"] == 76
        assert parsed["bat_temp"] == 21.5

    def test_cold_pack_keeps_zero_temperature(self) -> None:
        parsed = parse_bat_status_response(
            {"id": 1, "result": {"soc": 40, "bat_temp": 0.0, "rated_capacity": 5120}}
        )

        assert parsed["bat_temp"] == 0.0
        assert parsed["bat_rated_capacity"] == 5120


class TestEsModeSoc:
    """ES.GetMode ``bat_soc`` 0 only fills a gap."""

    def test_zero_does_not_replace_known_soc(self) -> None:
        mode = parse_es_mode_response({"id": 1, "result": {"mode": "Auto", "bat_soc": 0}})

        status = merge_device_status(es_mode_data=mode, previous_status={"battery_soc": 58})

        assert status["battery_soc"] == 58

    def test_zero_fills_an_empty_soc(self) -> None:
        mode = parse_es_mode_response({"id": 1, "result": {"mode": "Auto", "bat_soc": 0}})

        assert merge_device_status(es_mode_data=mode)["battery_soc"] == 0

    def test_es_status_zero_still_wins(self) -> None:
        mode = parse_es_mode_response({"id": 1, "result": {"mode": "Auto", "bat_soc": 0}})
        es = parse_es_status_response(_es_status(bat_soc=0, bat_cap=5120))

        status = merge_device_status(
            es_mode_data=mode, es_status_data=es, previous_status={"battery_soc": 3}
        )

        assert status["battery_soc"] == 0

    def test_nonzero_soc_updates_as_before(self) -> None:
        mode = parse_es_mode_response({"id": 1, "result": {"mode": "Auto", "bat_soc": 57}})

        status = merge_device_status(es_mode_data=mode, previous_status={"battery_soc": 58})

        assert status["battery_soc"] == 57

    def test_capacity_placeholder_keeps_previous_value(self) -> None:
        es = parse_es_status_response(_es_status(bat_soc=0, bat_cap=0))

        status = merge_device_status(
            es_status_data=es, previous_status={"battery_soc": 58, "bat_cap": 5120}
        )

        assert status["battery_soc"] == 58
        assert status["bat_cap"] == 5120
