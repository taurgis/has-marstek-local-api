"""The capacity card source keeps the reference layout and the HA registry."""

from __future__ import annotations

from pathlib import Path

_CARD = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "marstek"
    / "frontend"
    / "marstek-capacity-card.js"
)


def _source() -> str:
    return _CARD.read_text(encoding="utf-8")


def test_capacity_card_drops_the_semicircle_gauge() -> None:
    """The hero is tiles and a horizontal bar, not an SVG arc."""
    source = _source()
    assert "function gaugeSvg" not in source
    assert "function arcPath" not in source
    assert "function point(" not in source
    assert "overflow: visible" not in source
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in source
    assert 'class="pills"' in source
    assert "grid-template-columns: 1fr" not in source
    assert source.count("<colgroup>") == 2


def test_capacity_card_waits_for_the_home_assistant_element() -> None:
    """A cold dashboard must not define the strategy on the pre-polyfill registry."""
    source = _source()
    assert 'whenDefined("home-assistant")' in source
    assert "setTimeout(defineAll, 2000)" in source
    assert "window.customElements" in source


def test_capacity_card_editor_uses_one_label() -> None:
    """Optional fields do not get a second caption or a required asterisk."""
    source = _source()
    assert "control.required = false" in source
    assert "control.label" not in source
