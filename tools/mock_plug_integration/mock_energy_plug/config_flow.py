"""Ask for the plug's address."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import DOMAIN, TIMEOUT


class MockPlugConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a plug by host[:port]."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Check the plug answers, then add it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                url = f"http://{host}/rpc/Shelly.GetDeviceInfo"
                async with async_get_clientsession(self.hass).get(url, timeout=TIMEOUT) as resp:
                    resp.raise_for_status()
                    info = await resp.json()
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(str(info.get("id") or host))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Mock plug {host}", data={CONF_HOST: host})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )
