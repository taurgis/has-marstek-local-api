"""Fixtures shared by the test_config_flow tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
async def _drain_config_entry_tasks(hass: HomeAssistant) -> AsyncIterator[None]:
    """Finish create/reload tasks before HA 2026.9 lingering-timer checks.

    Lives in conftest rather than _helpers: an autouse fixture applies to the
    modules that can see it, and no test names it, so an imported one is just
    an unused import waiting to be removed.
    """
    yield
    await hass.async_block_till_done()
