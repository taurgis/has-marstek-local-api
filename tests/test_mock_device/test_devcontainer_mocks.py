"""Devcontainer mock lineup for firmware-specific wire encodings."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MOCK_DEVICE_ROOT = _REPO_ROOT / "tools" / "mock_device"


def test_devcontainer_runs_observed_firmware_mocks() -> None:
    """Compose must cover each observed family/firmware wire generation."""
    compose = (_REPO_ROOT / ".devcontainer" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert '"--device", "VenusA", "--ver", "148"' in compose
    assert '"--device", "VenusA", "--ver", "149"' in compose
    assert '"--device", "VenusA", "--ver", "150"' in compose
    assert '"--device", "VenusD", "--ver", "145"' in compose
    assert '"--device", "VenusE 3.0", "--ver", "150"' in compose
    assert '"--device", "VenusC", "--ver", "153"' in compose
    assert '"--device", "Venus E mini", "--ver", "145"' in compose
    assert '"--device", "VenusE", "--ver", "153"' in compose
    assert "172.28.0.26" in compose
    assert "172.28.0.27" in compose
    assert "172.28.0.28" in compose
    assert "172.28.0.29" in compose
    assert '"--port", "30004"' in compose


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
