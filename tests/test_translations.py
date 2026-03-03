"""Tests for translation structure validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.marstek.const import (
    CONF_ACTION_CHARGE_POWER,
    CONF_ACTION_DISCHARGE_POWER,
    CONF_FAILURE_THRESHOLD,
    CONF_PARALLEL_API_REQUESTS,
    CONF_POLL_INTERVAL_FAST,
    CONF_POLL_INTERVAL_MEDIUM,
    CONF_POLL_INTERVAL_SLOW,
    CONF_REQUEST_DELAY,
    CONF_REQUEST_TIMEOUT,
    CONF_SOCKET_LIMIT,
)

# Path to custom_components/marstek
COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "marstek"


@pytest.fixture
def strings_json() -> dict:
    """Load strings.json."""
    with open(COMPONENT_DIR / "strings.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def translations_en_json() -> dict:
    """Load translations/en.json."""
    with open(COMPONENT_DIR / "translations" / "en.json", encoding="utf-8") as f:
        return json.load(f)


class TestTranslationStructure:
    """Test translation file structure."""

    def test_strings_json_is_valid(self, strings_json: dict) -> None:
        """Test strings.json is valid JSON with expected top-level keys."""
        assert "config" in strings_json
        assert "options" in strings_json
        assert "entity" in strings_json

    def test_translations_en_json_is_valid(self, translations_en_json: dict) -> None:
        """Test translations/en.json is valid JSON with expected top-level keys."""
        assert "config" in translations_en_json
        assert "options" in translations_en_json
        assert "entity" in translations_en_json


class TestOptionsFlowSections:
    """Test options flow section translations."""

    # Define the expected section structure
    EXPECTED_SECTIONS = {
        "polling_settings": [
            CONF_POLL_INTERVAL_FAST,
            CONF_POLL_INTERVAL_MEDIUM,
            CONF_POLL_INTERVAL_SLOW,
        ],
        "network_settings": [
            CONF_PARALLEL_API_REQUESTS,
            CONF_REQUEST_DELAY,
            CONF_REQUEST_TIMEOUT,
            CONF_FAILURE_THRESHOLD,
        ],
        "power_settings": [
            CONF_ACTION_CHARGE_POWER,
            CONF_ACTION_DISCHARGE_POWER,
            CONF_SOCKET_LIMIT,
        ],
    }

    def _get_options_init_step(self, translations: dict) -> dict:
        """Get the options.step.init section from translations."""
        return translations["options"]["step"]["init"]

    def test_sections_exist(self, strings_json: dict) -> None:
        """Test that all expected sections exist in strings.json."""
        init_step = self._get_options_init_step(strings_json)
        assert "sections" in init_step, "Options init step must have 'sections'"

        sections = init_step["sections"]
        for section_name in self.EXPECTED_SECTIONS:
            assert section_name in sections, f"Missing section: {section_name}"

    def test_section_has_name_and_description(self, strings_json: dict) -> None:
        """Test that each section has a name and description."""
        sections = self._get_options_init_step(strings_json)["sections"]

        for section_name in self.EXPECTED_SECTIONS:
            section = sections[section_name]
            assert "name" in section, f"Section {section_name} missing 'name'"
            assert "description" in section, f"Section {section_name} missing 'description'"
            # Name should be human-readable (not the raw key)
            assert section["name"] != section_name, (
                f"Section {section_name} 'name' should be a human-readable label"
            )

    def test_section_fields_have_translations(self, strings_json: dict) -> None:
        """Test that all fields in each section have translations inside the section."""
        sections = self._get_options_init_step(strings_json)["sections"]

        for section_name, fields in self.EXPECTED_SECTIONS.items():
            section = sections[section_name]
            assert "data" in section, f"Section {section_name} missing 'data' for field labels"

            section_data = section["data"]
            for field in fields:
                assert field in section_data, (
                    f"Field '{field}' missing translation in section '{section_name}'. "
                    f"Field translations must be nested inside the section, not at the step level."
                )
                # Ensure the translation is not just the raw key
                assert section_data[field] != field, (
                    f"Field '{field}' in section '{section_name}' has raw key as label"
                )

    def test_section_fields_have_descriptions(self, strings_json: dict) -> None:
        """Test that all fields have data_description for help text."""
        sections = self._get_options_init_step(strings_json)["sections"]

        for section_name, fields in self.EXPECTED_SECTIONS.items():
            section = sections[section_name]
            assert "data_description" in section, (
                f"Section {section_name} missing 'data_description' for help text"
            )

            section_descriptions = section["data_description"]
            for field in fields:
                assert field in section_descriptions, (
                    f"Field '{field}' missing data_description in section '{section_name}'"
                )

    def test_no_orphaned_step_level_data(self, strings_json: dict) -> None:
        """Test that options.step.init does not have step-level 'data' key.

        When using sections, field translations should be inside each section,
        not at the step level. Step-level 'data' is ignored for sectioned forms.
        """
        init_step = self._get_options_init_step(strings_json)

        # With sections, there should be no step-level data/data_description
        # (they would be ignored anyway)
        if "sections" in init_step:
            assert "data" not in init_step, (
                "Step-level 'data' found in options.step.init with sections. "
                "Field translations should be inside each section, not at step level."
            )
            assert "data_description" not in init_step, (
                "Step-level 'data_description' found in options.step.init with sections. "
                "Field descriptions should be inside each section."
            )


class TestTranslationParity:
    """Test that strings.json and translations/en.json have matching structure."""

    def test_options_structure_matches(
        self, strings_json: dict, translations_en_json: dict
    ) -> None:
        """Test that options structure matches between strings.json and en.json."""
        strings_options = strings_json["options"]["step"]["init"]
        en_options = translations_en_json["options"]["step"]["init"]

        # Both should have same sections
        assert set(strings_options.get("sections", {}).keys()) == set(
            en_options.get("sections", {}).keys()
        ), "Section names differ between strings.json and en.json"

        # Each section should have same fields
        for section_name in strings_options.get("sections", {}):
            strings_section = strings_options["sections"][section_name]
            en_section = en_options["sections"][section_name]

            strings_data_keys = set(strings_section.get("data", {}).keys())
            en_data_keys = set(en_section.get("data", {}).keys())
            assert strings_data_keys == en_data_keys, (
                f"Field keys in section '{section_name}' differ: "
                f"strings.json has {strings_data_keys}, en.json has {en_data_keys}"
            )

    def test_config_flow_steps_match(
        self, strings_json: dict, translations_en_json: dict
    ) -> None:
        """Test that config flow steps match between strings.json and en.json."""
        strings_steps = set(strings_json["config"]["step"].keys())
        en_steps = set(translations_en_json["config"]["step"].keys())
        assert strings_steps == en_steps, (
            f"Config flow steps differ: strings.json has {strings_steps}, en.json has {en_steps}"
        )

    def test_error_keys_match(
        self, strings_json: dict, translations_en_json: dict
    ) -> None:
        """Test that error keys match between strings.json and en.json."""
        strings_errors = set(strings_json["config"]["error"].keys())
        en_errors = set(translations_en_json["config"]["error"].keys())
        assert strings_errors == en_errors, (
            f"Error keys differ: strings.json has {strings_errors}, en.json has {en_errors}"
        )

    def test_abort_keys_match(
        self, strings_json: dict, translations_en_json: dict
    ) -> None:
        """Test that abort keys match between strings.json and en.json."""
        strings_aborts = set(strings_json["config"]["abort"].keys())
        en_aborts = set(translations_en_json["config"]["abort"].keys())
        assert strings_aborts == en_aborts, (
            f"Abort keys differ: strings.json has {strings_aborts}, en.json has {en_aborts}"
        )
