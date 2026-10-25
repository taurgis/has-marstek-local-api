"""The battery-health module source keeps the HA registry and naming contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.marstek.helpers import capacity_frontend

_MODULE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "marstek"
    / "frontend"
    / "marstek-battery-health.js"
)


def _source() -> str:
    return _MODULE.read_text(encoding="utf-8")


def test_module_is_the_one_the_backend_serves() -> None:
    """The Python side registers this file and its strategy name."""
    assert _MODULE.name == capacity_frontend._MODULE_NAME
    strategy = capacity_frontend._STRATEGY.removeprefix("custom:")
    assert f'const STRATEGY = "{strategy}"' in _source()


def test_module_waits_for_the_home_assistant_element() -> None:
    """A cold dashboard must not define the strategy on the pre-polyfill registry."""
    source = _source()
    assert 'whenDefined("home-assistant")' in source
    assert "window.customElements" in source
    registration = source[source.index("function register()") :]
    assert "setTimeout(" not in registration


def test_tabs_follow_the_aria_tabs_pattern() -> None:
    """Tabs expose roles, the selected state and arrow-key navigation."""
    source = _source()
    for marker in (
        'role="tablist"',
        'role="tab"',
        'role="tabpanel"',
        "aria-selected",
        "ArrowRight",
    ):
        assert marker in source


def test_entity_kinds_are_real_translation_keys() -> None:
    """The frontend registry matches entities by translation key only."""
    match = re.search(r"const KINDS = \{(.*?)\};", _source(), re.S)
    assert match is not None
    keys = re.findall(r'"(\w+)"', match.group(1))
    strings = json.loads((_MODULE.parents[1] / "strings.json").read_text(encoding="utf-8"))
    known = {key for platform in strings["entity"].values() for key in platform}
    assert keys
    assert set(keys) <= known


@pytest.mark.parametrize(
    "name", ["marstek-battery-health-copy.js", "marstek-battery-health-plug.js"]
)
def test_imported_modules_load_with_the_card_token(name: str) -> None:
    """Each imported module uses this module's own ?v= token, so an update reaches the browser."""
    token = "${new URL(import.meta.url).search}"
    assert (_MODULE.parent / name).is_file()
    assert f"import(`./{name}{token}`)" in _source()


def test_every_language_has_the_same_text_keys() -> None:
    """A key missing in one language would render as undefined."""
    copy = (_MODULE.parent / "marstek-battery-health-copy.js").read_text(encoding="utf-8")
    en, nl = re.split(r"^  nl: \{$", copy, flags=re.M)
    keys = [re.findall(r"^    (\w+):", part, re.M) for part in (en, nl)]
    assert keys[0]
    assert keys[0] == keys[1]
