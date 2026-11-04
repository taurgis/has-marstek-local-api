"""Keep Home Assistant imports on the public surface of each integration.

Core moves internal modules without a deprecation period. ``http.server``
arrived in 2026.8 and ``binary_sensor.const`` in 2026.10, so importing them
makes the integration fail to load on Home Assistant 2025.10 through 2026.5
(issue #155) even though ``hacs.json`` still allows those versions.

The supported import is the integration package
(``homeassistant.components.http``). A deeper
``homeassistant.components.<domain>.<submodule>`` path is rejected unless it
is listed in ``ALLOWED_COMPONENT_SUBMODULES``. Helper paths deeper than one
module need the same treatment; ``helpers.service_info.dhcp`` is the
documented DHCP discovery import and has existed since before 2025.10:

https://developers.home-assistant.io/docs/core/platform/dhcp/
https://developers.home-assistant.io/blog/2024/06/18/async_register_static_paths/
https://developers.home-assistant.io/docs/deprecating/
"""

from __future__ import annotations

import ast
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "marstek"

# Public component submodules that have existed since Home Assistant 2025.10,
# the minimum in hacs.json. Leave this empty unless a package re-export does
# not exist. Each entry needs a comment naming the oldest Core version that
# already shipped that module.
ALLOWED_COMPONENT_SUBMODULES: frozenset[str] = frozenset()

# Documented helper imports that are not a single public module.
ALLOWED_HELPER_SUBMODULES: frozenset[str] = frozenset(
    {
        "homeassistant.helpers.service_info.dhcp",
    }
)


def _imported_modules(source: str) -> list[tuple[int, str]]:
    """Return ``(line, module)`` for every import in ``source``."""
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


def _is_private_home_assistant_import(module: str) -> bool:
    """Return whether ``module`` is an internal Home Assistant path."""
    parts = module.split(".")
    if len(parts) < 4 or parts[0] != "homeassistant":
        return False
    if parts[1] == "components":
        return module not in ALLOWED_COMPONENT_SUBMODULES
    if parts[1] in {"helpers", "util"}:
        return module not in ALLOWED_HELPER_SUBMODULES
    return False


def test_integration_imports_stay_on_the_public_home_assistant_surface() -> None:
    """The integration package does not import private component submodules."""
    violations: list[str] = []
    for path in sorted(COMPONENT_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for lineno, module in _imported_modules(source):
            if _is_private_home_assistant_import(module):
                relative = path.relative_to(COMPONENT_ROOT.parents[1])
                violations.append(f"{relative}:{lineno}: {module}")
    assert not violations, (
        "Import the integration package, not an internal submodule "
        "(see ALLOWED_COMPONENT_SUBMODULES when a path is public and "
        "already shipped in Home Assistant 2025.10):\n" + "\n".join(violations)
    )


def test_private_component_submodule_imports_are_rejected() -> None:
    """The guard catches the imports that broke Home Assistant 2026.5.4."""
    source = "\n".join(
        [
            "from homeassistant.components.http.server import StaticPathConfig",
            "from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass",
            "from homeassistant.components.http import StaticPathConfig",
            "from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo",
            "from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback",
            "import homeassistant.components.lovelace.const",
        ]
    )
    private = [
        module
        for _, module in _imported_modules(source)
        if _is_private_home_assistant_import(module)
    ]
    assert private == [
        "homeassistant.components.http.server",
        "homeassistant.components.binary_sensor.const",
        "homeassistant.components.lovelace.const",
    ]
