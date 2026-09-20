"""Fixtures shared by the test_scanner tests."""

from __future__ import annotations

import pytest

from custom_components.marstek.scanner import MarstekScanner


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the scanner singleton before each test."""
    MarstekScanner._scanner = None
    yield
    MarstekScanner._scanner = None
