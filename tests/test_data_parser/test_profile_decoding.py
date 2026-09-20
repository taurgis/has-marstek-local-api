"""Decoding the firmware profile the parsers key their behaviour on."""

from __future__ import annotations

from custom_components.marstek.firmware_profile import resolve_firmware_profile
from custom_components.marstek.pymarstek.data_parser import (
    merge_device_status,
    parse_bat_status_response,
    parse_em_status_response,
    parse_es_mode_response,
    parse_es_status_response,
    parse_pv_status_response,
)


class TestFirmwareProfileDecoding:
    """Decode energy and power using the firmware profile contract."""

    def test_venus_a_149_scales_solar_energy_but_not_grid_totals(self) -> None:
        """Raw solar 25742 becomes 257420 Wh; grid totals stay unscaled."""
        profile = resolve_firmware_profile("VenusA", 149)
        result = parse_es_status_response(
            {
                "id": 1,
                "result": {
                    "bat_soc": 55,
                    "total_pv_energy": 25742,
                    "total_grid_input_energy": 1607,
                    "total_grid_output_energy": 844,
                    "total_load_energy": 321,
                },
            },
            profile,
        )

        assert result["total_pv_energy"] == 257420
        assert result["total_grid_input_energy"] == 1607
        assert result["total_grid_output_energy"] == 844
        assert result["total_load_energy"] == 321

    def test_legacy_solar_energy_stays_in_wh(self) -> None:
        """A known legacy profile leaves solar energy unchanged."""
        profile = resolve_firmware_profile("VenusD", 145)
        result = parse_es_status_response(
            {
                "id": 1,
                "result": {"total_pv_energy": 257420},
            },
            profile,
        )

        assert result["total_pv_energy"] == 257420

    def test_unknown_firmware_does_not_guess_rev31_solar_scale(self) -> None:
        """Unparseable firmware follows the legacy-safe profile contract."""
        profile = resolve_firmware_profile("VenusA", "not-a-version")
        result = parse_es_status_response(
            {
                "id": 1,
                "result": {"total_pv_energy": 25742},
            },
            profile,
        )

        assert profile.pv_energy_scale == 1.0
        assert result["total_pv_energy"] == 25742

    def test_venus_a_148_dot_label_does_not_scale_solar_energy(self) -> None:
        """App firmware 148.3 follows the Open API 148 legacy solar unit."""
        profile = resolve_firmware_profile("VenusA", "148.3")
        result = parse_es_status_response(
            {
                "id": 1,
                "result": {"total_pv_energy": 25742},
            },
            profile,
        )

        assert profile.firmware_version == 148
        assert profile.pv_energy_scale == 1.0
        assert result["total_pv_energy"] == 25742

    def test_legacy_pv_channel_1_deciwatts_decode_to_watts(self) -> None:
        """Legacy channel 1 3200 becomes 320 W; other channels stay watts."""
        profile = resolve_firmware_profile("VenusD", 145)
        result = parse_pv_status_response(
            {
                "id": 1,
                "result": {
                    "pv1_power": 3200,
                    "pv2_power": 280,
                },
            },
            profile,
        )

        assert result["pv1_power"] == 320
        assert result["pv2_power"] == 280

    def test_venus_a_148_dot_label_keeps_channel_1_deciwatts(self) -> None:
        """App firmware 148.3 must not switch channel 1 to watts."""
        profile = resolve_firmware_profile("VenusA", "148.3")
        result = parse_pv_status_response(
            {
                "id": 1,
                "result": {
                    "pv1_power": 3200,
                    "pv2_power": 280,
                },
            },
            profile,
        )

        assert profile.pv_channel_1_power_scale == 0.1
        assert result["pv1_power"] == 320
        assert result["pv2_power"] == 280

    def test_venus_a_150_dot_9_keeps_channel_1_deciwatts(self) -> None:
        """App firmware 150.9 must keep PV1 ÷10 (#57); solar energy stays scaled (#35)."""
        profile = resolve_firmware_profile("VenusA", "150.9")
        pv_result = parse_pv_status_response(
            {
                "id": 1,
                "result": {
                    "pv1_power": 3200,
                    "pv2_power": 280,
                },
            },
            profile,
        )
        es_result = parse_es_status_response(
            {
                "id": 1,
                "result": {"total_pv_energy": 25742},
            },
            profile,
        )

        assert profile.firmware_version == 150
        assert profile.pv_channel_1_power_scale == 0.1
        assert profile.pv_energy_scale == 10.0
        assert pv_result["pv1_power"] == 320
        assert pv_result["pv2_power"] == 280
        assert es_result["total_pv_energy"] == 257420

    def test_firmware_150_pv_channel_1_stays_deciwatts(self) -> None:
        """Integer ver 150 still divides channel 1; other channels stay watts."""
        profile = resolve_firmware_profile("VenusA", 150)
        result = parse_pv_status_response(
            {
                "id": 1,
                "result": {
                    "pv1_power": 3200,
                    "pv2_power": 280,
                },
            },
            profile,
        )

        assert result["pv1_power"] == 320
        assert result["pv2_power"] == 280

        single = parse_pv_status_response(
            {"id": 1, "result": {"pv_power": 3200}},
            profile,
        )
        assert single["pv1_power"] == 320

    def test_em_energy_decodes_from_deciwatt_hours_including_zero(self) -> None:
        """EM lifetime energy uses 0.1 Wh wire units, including zero."""
        profile = resolve_firmware_profile("VenusE 3.0", 150)
        result = parse_em_status_response(
            {
                "id": 1,
                "result": {
                    "ct_state": 1,
                    "input_energy": 3086320,
                    "output_energy": 0,
                },
            },
            profile,
        )

        assert result["em_input_energy"] == 308632
        assert result["em_output_energy"] == 0
        assert "em_input_energy" in result

        fractional = parse_em_status_response(
            {
                "id": 1,
                "result": {
                    "input_energy": 1,
                    "output_energy": 15,
                },
            },
            profile,
        )
        assert fractional["em_input_energy"] == 0.1
        assert fractional["em_output_energy"] == 1.5

    def test_em_energy_fields_stay_missing_when_absent(self) -> None:
        """Missing EM energy fields are not manufactured as zero."""
        profile = resolve_firmware_profile("VenusE 3.0", 150)
        result = parse_em_status_response(
            {"id": 1, "result": {"ct_state": 1, "total_power": 10}},
            profile,
        )

        assert "em_input_energy" not in result
        assert "em_output_energy" not in result

    def test_es_get_mode_fills_missing_em_fields(self) -> None:
        """Mode CT/power/energy fill coordinator keys when EM is absent."""
        profile = resolve_firmware_profile("VenusE 3.0", 150)
        mode = parse_es_mode_response(
            {
                "id": 1,
                "result": {
                    "mode": "Auto",
                    "ct_state": 1,
                    "a_power": 10,
                    "b_power": 11,
                    "c_power": 12,
                    "total_power": 33,
                    "input_energy": 3086320,
                    "output_energy": 4487510,
                },
            },
            profile,
        )
        merged = merge_device_status(es_mode_data=mode)

        assert merged["ct_state"] == 1
        assert merged["em_a_power"] == 10
        assert merged["em_total_power"] == 33
        assert merged["em_input_energy"] == 308632
        assert merged["em_output_energy"] == 448751

    def test_em_values_win_field_by_field_over_mode(self) -> None:
        """Current EM.GetStatus values beat overlapping ES.GetMode fallbacks."""
        profile = resolve_firmware_profile("VenusE 3.0", 150)
        mode = parse_es_mode_response(
            {
                "id": 1,
                "result": {
                    "mode": "Auto",
                    "ct_state": 0,
                    "a_power": 1,
                    "total_power": 1,
                    "input_energy": 10,
                    "output_energy": 20,
                },
            },
            profile,
        )
        em = parse_em_status_response(
            {
                "id": 1,
                "result": {
                    "ct_state": 1,
                    "a_power": 120,
                    "total_power": 360,
                    "input_energy": 3086320,
                },
            },
            profile,
        )
        merged = merge_device_status(es_mode_data=mode, em_status_data=em)

        assert merged["ct_state"] == 1
        assert merged["em_a_power"] == 120
        assert merged["em_total_power"] == 360
        assert merged["em_input_energy"] == 308632
        assert merged["em_output_energy"] == 2.0

    def test_getmode_zeros_do_not_clobber_previous_em_values(self) -> None:
        """Venus E 150 GetMode CT zeros must not wipe last-known EM readings."""
        profile = resolve_firmware_profile("VenusE 3.0", 150)
        previous = {
            "ct_state": 1,
            "ct_connected": True,
            "em_a_power": 2581,
            "em_b_power": 0,
            "em_c_power": 0,
            "em_total_power": 2581,
            "em_input_energy": 0.0,
            "em_output_energy": 0.0,
        }
        mode = parse_es_mode_response(
            {
                "id": 2,
                "result": {
                    "id": 0,
                    "mode": "Auto",
                    "ongrid_power": 1246,
                    "offgrid_power": 0,
                    "bat_soc": 52,
                    "ct_state": 0,
                    "a_power": 0,
                    "b_power": 0,
                    "c_power": 0,
                    "total_power": 0,
                    "input_energy": 0,
                    "output_energy": 0,
                },
            },
            profile,
        )
        merged = merge_device_status(es_mode_data=mode, previous_status=previous)

        assert merged["device_mode"] == "auto"
        assert merged["ongrid_power"] == 1246
        assert merged["offgrid_power"] == 0
        assert merged["ct_state"] == 1
        assert merged["em_a_power"] == 2581
        assert merged["em_total_power"] == 2581

    def test_venus_e_150_observed_payloads_merge_like_the_lan_capture(self) -> None:
        """Parser output matches the 2026-09-15 Venus E 3.0 / ver 150 GET capture."""
        profile = resolve_firmware_profile("VenusE 3.0", 150)
        assert profile.supports_pv is False
        assert profile.supports_ups is True
        assert profile.supports_em_energy is True
        assert profile.em_energy_scale == 0.1

        mode = parse_es_mode_response(
            {
                "id": 2,
                "result": {
                    "id": 0,
                    "mode": "Auto",
                    "ongrid_power": 1246,
                    "offgrid_power": 0,
                    "bat_soc": 52,
                    "ct_state": 0,
                    "a_power": 0,
                    "b_power": 0,
                    "c_power": 0,
                    "total_power": 0,
                    "input_energy": 0,
                    "output_energy": 0,
                },
            },
            profile,
        )
        es_status = parse_es_status_response(
            {
                "id": 4,
                "result": {
                    "id": 0,
                    "bat_soc": 52,
                    "bat_cap": 5120,
                    "pv_power": 0,
                    "ongrid_power": 1246,
                    "offgrid_power": 0,
                    "total_pv_energy": 0,
                    "total_grid_output_energy": 969749,
                    "total_grid_input_energy": 1167238,
                    "total_load_energy": 0,
                },
            },
            profile,
        )
        em = parse_em_status_response(
            {
                "id": 5,
                "result": {
                    "id": 0,
                    "ct_state": 1,
                    "a_power": 2581,
                    "b_power": 0,
                    "c_power": 0,
                    "total_power": 2581,
                    "input_energy": 0,
                    "output_energy": 0,
                },
            },
            profile,
        )
        bat = parse_bat_status_response(
            {
                "id": 8,
                "result": {
                    "id": 0,
                    "soc": 51,
                    "charg_flag": True,
                    "dischrg_flag": True,
                    "bat_temp": 31.0,
                    "bat_capacity": 2652.0,
                    "rated_capacity": 5120.0,
                },
            }
        )
        merged = merge_device_status(
            es_mode_data=mode,
            es_status_data=es_status,
            em_status_data=em,
            bat_status_data=bat,
        )

        assert merged["device_mode"] == "auto"
        assert merged["battery_soc"] == 52
        assert merged["battery_power"] == 1246
        assert merged["battery_status"] == "discharging"
        assert merged["ct_state"] == 1
        assert merged["em_total_power"] == 2581
        assert merged["em_input_energy"] == 0
        assert merged["em_output_energy"] == 0
        assert merged["total_grid_input_energy"] == 1167238
        assert merged["bat_temp"] == 31.0
        assert merged["bat_soc_detailed"] == 51
