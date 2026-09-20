"""Shared SYS write helper for optimistic configuration entities."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import HomeAssistantError

from ..const import (
    CONF_REQUEST_TIMEOUT,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_UDP_PORT,
    DOMAIN,
)
from ..pymarstek import MarstekUDPClient, ValidationError
from .polling import polling_paused


def sys_write_target(config_entry: ConfigEntry) -> tuple[str, int]:
    """Return the configured host and port for a SYS write."""
    host = config_entry.data.get(CONF_HOST)
    if not host:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_host_configured",
        )
    port = config_entry.data.get(CONF_PORT, DEFAULT_UDP_PORT)
    return str(host), int(port)


def sys_write_timeout(config_entry: ConfigEntry) -> float:
    """Return the configured UDP timeout for a SYS write."""
    return float(config_entry.options.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))


def require_sys_write_ack(response: Any) -> None:
    """Raise if the device did not acknowledge a SYS write."""
    if not isinstance(response, dict):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="sys_write_rejected",
        )
    if "error" in response:
        error = response.get("error")
        if isinstance(error, dict):
            code = str(error.get("code", "unknown"))
            message = str(error.get("message", "unknown"))
        else:
            code = "unknown"
            message = str(error)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="sys_write_rpc_error",
            translation_placeholders={"code": code, "message": message},
        )
    result = response.get("result")
    if not isinstance(result, dict) or result.get("set_result") is not True:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="sys_write_rejected",
        )


async def async_send_sys_write(
    udp_client: MarstekUDPClient,
    command: str,
    host: str,
    port: int,
    timeout: float,
) -> None:
    """Pause polling, send a SYS write, and require set_result acknowledgement."""
    async with polling_paused(udp_client, host):
        try:
            response = await udp_client.send_request(
                command,
                host,
                port,
                timeout=timeout,
            )
        except TimeoutError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="sys_write_timeout",
            ) from err
        except ValidationError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="sys_write_invalid",
                translation_placeholders={"error": err.message},
            ) from err
        except (OSError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="sys_write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        require_sys_write_ack(response)
