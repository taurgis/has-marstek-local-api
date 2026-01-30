---
name: homeassistant-config-flow
description: Patterns for Home Assistant config flows, discovery handlers, options flows, and reauth for custom components
---

# Home Assistant Config Flow & Discovery

Use this skill when adding or adjusting setup flows, discovery handlers, options flows, or reauth for this integration.

## When to Use
- Creating or refining `config_flow.py`
- Handling discovery sources (user, dhcp, zeroconf, integration discovery)
- Adding/updating options flow or reauth steps
- Ensuring duplicate prevention and IP-change handling are correct

## Core Principles
- **UI-first**: No new YAML; all setup in the UI.
- **Async-only**: All I/O must be awaited; use executor only for blocking library work.
- **One device, one entry**: Abort duplicates; update existing entries when discovery reports a new host/IP.
- **Stable identifiers**: Use BLE-MAC (or WiFi MAC) for unique IDs; never pivot unique IDs on IP.
- **Selectors > raw text**: Prefer HA selectors for better UX and validation.

## Step Patterns
- `async_step_user`: show form when `user_input is None`; on submit, validate connectivity; return errors with keys (`cannot_connect`, `invalid_auth`, `already_configured`).
- `async_step_dhcp` / `async_step_zeroconf` / `async_step_integration_discovery`: deduplicate via MAC/unique ID; if existing entry with new host, update data and abort with `already_configured`.
- `async_step_confirm`: for discovery flows, prefill known values and ask user to confirm.

## Reauth Flow Pattern

Reauth handles authentication/connection failures gracefully, allowing users to update credentials or IP addresses without removing the integration.

### Implementation Steps

1. **Entry point**: `async_step_reauth(entry_data)` - Called when reauth is triggered
2. **Confirm step**: `async_step_reauth_confirm(user_input)` - Show form, validate, update entry

### Code Pattern

```python
from homeassistant.config_entries import SOURCE_REAUTH

async def async_step_reauth(
    self, entry_data: dict[str, Any]
) -> ConfigFlowResult:
    """Handle reauth when device becomes unreachable."""
    return await self.async_step_reauth_confirm()

async def async_step_reauth_confirm(
    self, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    """Confirm reauth dialog."""
    errors: dict[str, str] = {}
    reauth_entry = self._get_reauth_entry()

    if user_input is not None:
        host = user_input.get(CONF_HOST)
        try:
            device_info = await get_device_info(host=host, port=port)
            if device_info:
                # Validate unique ID unchanged
                await self.async_set_unique_id(device_info["ble_mac"])
                self._abort_if_unique_id_mismatch()
                # Preferred: use async_update_reload_and_abort helper
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_HOST: host},
                )
            errors["base"] = "cannot_connect"
        except (OSError, TimeoutError):
            errors["base"] = "cannot_connect"

    return self.async_show_form(
        step_id="reauth_confirm",
        data_schema=vol.Schema({vol.Required(CONF_HOST): cv.string}),
        errors=errors,
        description_placeholders={"host": reauth_entry.data.get(CONF_HOST, "")},
    )
```

### Key Helpers

- `self._get_reauth_entry()` - Get the config entry being reauthenticated
- `self.async_update_reload_and_abort()` - Update entry, reload, and abort with `reauth_successful` (preferred)
- `self._abort_if_unique_id_mismatch()` - Ensure the same device is being reauthenticated
- Check source: `if self.source == SOURCE_REAUTH:`

### Triggering Reauth

From coordinator or setup when connection fails repeatedly:
```python
entry.async_start_reauth(hass)
```

### Required Translations

Add to `strings.json` and `translations/en.json`:
```json
"reauth_confirm": {
  "title": "Reconnect Device",
  "description": "The device at {host} is not responding.",
  "data": {"host": "IP address"}
}
```
And abort reason:
```json
"abort": {
  "reauth_successful": "Device reconnected successfully"
}
```

## Form Patterns

### Grouping Input Fields (sections)
Use `section()` to group related fields into collapsible sections:
```python
from homeassistant.data_entry_flow import section

data_schema = {
    vol.Required("host"): str,
    vol.Required("advanced_options"): section(
        vol.Schema({
            vol.Optional("timeout", default=30): int,
            vol.Optional("retry_count", default=3): int,
        }),
        {"collapsed": True},  # Initially collapsed
    )
}
```

### Pre-filling Forms with Suggested Values
```python
from homeassistant.helpers.schema_config_entry_flow import add_suggested_values_to_schema

return self.async_show_form(
    data_schema=add_suggested_values_to_schema(
        OPTIONS_SCHEMA, self.config_entry.options
    )
)
```

### Browser Autofill
Use recognized keys (`username`, `password`) or `TextSelector` with `autocomplete`:
```python
vol.Required(CONF_USERNAME): TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
),
```

### Read-only Fields in Options
For frozen configuration shown in options:
```python
vol.Optional(CONF_DEVICE_ID): EntitySelector(
    EntitySelectorConfig(read_only=True)
),
```

### Navigation Menu
```python
return self.async_show_menu(
    step_id="user",
    menu_options=["discovery", "manual"],
    description_placeholders={"model": "Venus A"},
)
```

### Long-running Tasks (Show Progress)
```python
if not self.task:
    self.task = self.hass.async_create_task(long_running_operation())
if not self.task.done():
    return self.async_show_progress(
        progress_action="connecting",
        progress_task=self.task,
    )
return self.async_show_progress_done(next_step_id="finish")
```

## Options Flow
- Implement `async_get_options_flow` to return an `OptionsFlowHandler`.
- Store runtime preferences in `entry.options`; keep credentials in `entry.data`.
- Register `entry.add_update_listener` in `__init__.py` to reload on options change.
- Use selectors for numeric intervals, toggles, enums, and text fields where helpful.
- Use `add_suggested_values_to_schema()` to pre-fill current options.

## Discovery Handling
- Manifest-driven discovery (dhcp/zeroconf/ssdp) should land in dedicated steps.
- The scanner handles IP changes; do not add fallback discovery inside coordinator updates.
- On discovery, set unique ID early and call `self._abort_if_unique_id_configured()`.
- If the device is known but host changed, update the entry data and abort with `already_configured`.

## Error Keys & Translations
- Define user-facing errors in `strings.json` and mirror to `translations/en.json`.
- Preferred keys: `cannot_connect`, `invalid_auth`, `already_configured`, `unknown`.
- Provide helpful `description_placeholders` when useful (e.g., `{ip_address}`).

## Form Translation Structure

Config flow forms are translated via `strings.json`:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Set up Marstek",
        "description": "Connect to your {model} device.",
        "data": {
          "host": "Hostname or IP",
          "port": "Port"
        },
        "data_description": {
          "host": "The IP address of your Marstek device on the local network"
        },
        "sections": {
          "advanced_options": {
            "name": "Advanced Options",
            "description": "Optional configuration"
          }
        }
      }
    },
    "error": {
      "cannot_connect": "Cannot connect to device",
      "invalid_auth": "Invalid authentication"
    },
    "abort": {
      "already_configured": "Device is already configured",
      "reauth_successful": "Reauthentication successful"
    }
  }
}
```

**Form translation keys:**
- `title` - Form title
- `description` - Form description (supports placeholders via `description_placeholders`)
- `data` - Field labels (keyed by field name)
- `data_description` - Field help text (shown below field)
- `sections` - Section names and descriptions (for `section()` fields)

## Validation Checklist
- [ ] Unique ID set from BLE-MAC/WiFi MAC; duplicates abort
- [ ] User step validates connectivity asynchronously
- [ ] Discovery steps update existing entry on host/IP change
- [ ] Options flow reloads entry on change
- [ ] Reauth asks only for the changed credential
- [ ] Selectors used where appropriate; translations updated
