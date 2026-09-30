"""Devcontainer mock lineup for firmware-specific wire encodings."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MOCK_DEVICE_ROOT = _REPO_ROOT / "tools" / "mock_device"
_COMPOSE = _REPO_ROOT / ".devcontainer" / "docker-compose.yml"
_CATALOG = _REPO_ROOT / "tools" / "firmware" / "catalog.json"

# Archived Control images that should have a dedicated Docker mock.
# (device CLI string, Open API ver)
_ARCHIVED_CONTROL_MOCKS: frozenset[tuple[str, int]] = frozenset(
    {
        ("VenusE 3.0", 144),
        ("VenusE 3.0", 147),
        ("VenusE 3.0", 1476),
        ("VenusE 3.0", 148),
        ("VenusE 3.0", 149),
        ("VenusE 3.0", 150),
        ("VenusE 3.0", 151),
        ("VenusA", 148),
        ("VenusA", 1487),
        ("VenusA", 149),
        ("VenusA", 150),
        ("VenusA", 1509),
        ("VenusE Pro", 1508),
        ("VenusD", 147),
        ("VenusD", 149),
        ("VenusD", 1492),
        ("VenusD", 150),
        ("VenusD", 151),
        ("VenusC", 153),
        ("VenusC", 155),
        ("VenusC", 156),
        ("VenusE", 153),
        ("VenusE", 155),
        ("VenusE", 156),
    }
)


def _compose_device_versions() -> set[tuple[str, int]]:
    """Return (--device, --ver) pairs from docker-compose mock commands."""
    compose = _COMPOSE.read_text(encoding="utf-8")
    found: set[tuple[str, int]] = set()
    default_device = "VenusE 3.0"
    for command in re.findall(r"command: (\[.*?\])", compose):
        args = ast.literal_eval(command)
        device = default_device
        ver: int | None = None
        pending: str | None = None
        for token in args:
            if pending == "device":
                device = str(token)
                pending = None
            elif pending == "ver":
                ver = int(token)
                pending = None
            elif token == "--device":
                pending = "device"
            elif token == "--ver":
                pending = "ver"
        if ver is not None:
            found.add((device, ver))
    return found


def test_devcontainer_runs_observed_firmware_mocks() -> None:
    """Compose must cover each observed family/firmware wire generation."""
    compose = _COMPOSE.read_text(encoding="utf-8")

    assert '"--device", "VenusA", "--ver", "148"' in compose
    assert '"--device", "VenusA", "--ver", "149"' in compose
    assert '"--device", "VenusA", "--ver", "150"' in compose
    assert '"--device", "VenusA", "--ver", "1487"' in compose
    assert '"--device", "VenusA", "--ver", "1509"' in compose
    assert '"--device", "VenusD", "--ver", "145"' in compose
    assert '"--device", "VenusD", "--ver", "147"' in compose
    assert '"--device", "VenusD", "--ver", "149"' in compose
    assert '"--device", "VenusD", "--ver", "1492"' in compose
    assert '"--device", "VenusD", "--ver", "150"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "144"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "147"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "1476"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "148"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "149"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "150"' in compose
    assert '"--device", "VenusC", "--ver", "153"' in compose
    assert '"--device", "VenusC", "--ver", "155"' in compose
    assert '"--device", "VenusC", "--ver", "156"' in compose
    assert '"--device", "Venus E mini", "--ver", "145"' in compose
    assert '"--device", "VNSEM-0", "--ver", "301"' in compose
    assert '"--device", "VenusE", "--ver", "153"' in compose
    assert '"--device", "VenusE", "--ver", "155"' in compose
    assert '"--device", "VenusE", "--ver", "156"' in compose
    assert '"--device", "VenusE Pro", "--ver", "1508"' in compose
    assert "172.28.0.26" in compose
    assert "172.28.0.46" in compose
    assert "172.28.0.48" in compose


def test_devcontainer_covers_archived_control_images() -> None:
    """Every archived Control device/ver combo has a compose mock."""
    running = _compose_device_versions()
    missing = sorted(_ARCHIVED_CONTROL_MOCKS - running)
    assert missing == [], f"compose missing Control mocks: {missing}"


def test_firmware_catalog_lists_control_images_for_mocks() -> None:
    """catalog.json tracks the Control blobs the Docker matrix is based on."""
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    versions_by_sku: dict[str, set[int]] = {}
    for image in catalog["images"]:
        assert image["firmwareType"] == "Control"
        sku = str(image["deviceType"])
        versions_by_sku.setdefault(sku, set()).add(int(image["version"]))

    assert versions_by_sku["VNSE3-0"] == {144, 147, 1476, 148, 149, 150, 151}
    assert versions_by_sku["VNSA-0"] == {148, 1487, 149, 150, 1508, 1509}
    assert versions_by_sku["VNSD-0"] == {147, 149, 1492, 150, 151}
    assert versions_by_sku["HMG-50"] == {153, 155, 156}


def _mock_custom_component_modules() -> set[str]:
    """Return absolute custom_components modules imported by the mock package."""
    modules: set[str] = set()
    for path in _MOCK_DEVICE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("custom_components."):
                    modules.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("custom_components."):
                        modules.add(alias.name)
    return modules


def test_dockerfile_copies_custom_component_modules_the_mock_imports() -> None:
    """Docker mocks must ship every custom_components module the simulator imports."""
    dockerfile = (_MOCK_DEVICE_ROOT / "Dockerfile").read_text(encoding="utf-8")
    modules = _mock_custom_component_modules()
    assert modules, "mock_device is expected to import canonical custom_components modules"

    for module in sorted(modules):
        relative_py = module.replace(".", "/") + ".py"
        assert relative_py in dockerfile, (
            f"tools/mock_device/Dockerfile must COPY {relative_py} "
            f"(imported by the mock as {module})"
        )


_SKU_BY_MOCK_DEVICE = {
    "VenusE 3.0": "VNSE3-0",
    "VenusA": "VNSA-0",
    "VenusE Pro": "VNSA-0",
    "VenusD": "VNSD-0",
    "VenusC": "HMG-50",
    "VenusE": "HMG-50",
}


def _compose_services() -> dict[str, dict[str, Any]]:
    services: dict[str, dict[str, Any]] = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))[
        "services"
    ]
    return services


def _flag(command: list[str], name: str, default: str) -> str:
    return command[command.index(name) + 1] if name in command else default


def test_default_mocks_do_not_duplicate_a_firmware_emulator() -> None:
    """A mock starts by default only where no catalogued firmware image exists."""
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    images = {(str(i["deviceType"]), int(i["version"])) for i in catalog["images"]}
    duplicated_by_default = []
    for name, service in _compose_services().items():
        if not name.startswith("mock-marstek") or service.get("profiles"):
            continue
        command = service["command"]
        device = _flag(command, "--device", "VenusE 3.0")
        ver = int(_flag(command, "--ver", "145"))
        if (_SKU_BY_MOCK_DEVICE.get(device), ver) in images:
            duplicated_by_default.append(name)
    assert duplicated_by_default == []


def test_every_catalogued_image_has_a_firmware_emulator() -> None:
    """Each catalog image runs as a fw-* service, so a profiled mock is never the only copy."""
    catalog = json.loads(_CATALOG.read_text(encoding="utf-8"))
    images = {f"{i['deviceType']}:{i['version']}" for i in catalog["images"]}
    emulated = {
        _flag(service["command"], "--firmware", "")
        for name, service in _compose_services().items()
        if name.startswith("fw-") and not name.endswith("-ct")
    }
    assert images - emulated == set()


def test_custom_port_emulators_probe_their_own_port() -> None:
    """The per-port UDP pool keeps its custom ports, now on real firmware."""
    ports: dict[str, str] = {}
    for name, service in _compose_services().items():
        if not name.startswith("fw-") or name.endswith("-ct"):
            continue
        port = _flag(service["command"], "--local-api-port", "30000")
        if port == "30000":
            assert "healthcheck" not in service, name
            continue
        ports[name] = port
        assert service["healthcheck"]["test"][-2:] == ["--port", port], name
    assert ports == {
        "fw-venus-a-148": "30001",
        "fw-venus-a-149": "30003",
        "fw-venus-a-150": "30004",
    }
    assert "profiles" not in _compose_services()["fw-venus-a-150"]


def test_readme_test_matrix_names_every_emulator_and_default_mock() -> None:
    """The README matrix is how owners see their coverage, so it must not drift."""
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    matrix = readme.split("## Test matrix", 1)[1].split("\n## ", 1)[0]
    services = _compose_services()
    expected = {
        name
        for name, service in services.items()
        if (name.startswith("fw-") and not name.endswith("-ct"))
        or (name.startswith("mock-marstek") and "profiles" not in service)
    }
    missing = {name for name in expected if f"`{name}`" not in matrix}
    assert not missing, f"README test matrix lacks {sorted(missing)}"
    template = "firmware_coverage.yml"
    assert f"issues/new?template={template}" in matrix
    assert (_REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / template).is_file()
