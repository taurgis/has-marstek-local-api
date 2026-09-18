"""Devcontainer mock lineup for firmware-specific wire encodings."""

from __future__ import annotations

from pathlib import Path


def test_devcontainer_runs_venus_a_148_and_149_mocks() -> None:
    """Compose must expose both Venus A solar encodings: 148-or-older and 149."""
    compose = (
        Path(__file__).resolve().parents[2] / ".devcontainer" / "docker-compose.yml"
    ).read_text(encoding="utf-8")

    assert '"--device", "VenusA", "--ver", "148"' in compose
    assert '"--device", "VenusA", "--ver", "149"' in compose
    assert '"--device", "VenusD", "--ver", "145"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "150"' in compose
    assert '"--device", "VenusA", "--ver", "150"' not in compose
