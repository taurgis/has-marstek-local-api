"""The minimum-version boot reads hacs.json instead of a hard-coded tag."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _checker():
    """Load the CI script without running it."""
    path = ROOT / "scripts" / "check_minimum_home_assistant.py"
    spec = importlib.util.spec_from_file_location("check_minimum_home_assistant", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_minimum_version_comes_from_hacs_json() -> None:
    """The Docker tag is the version HACS uses to block older Core installs."""
    declared = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]
    assert _checker().minimum_version(ROOT) == declared


def test_workflow_invokes_the_hacs_version_script() -> None:
    """CI must not pin a Core tag of its own."""
    workflow = (ROOT / ".github" / "workflows" / "minimum-ha.yaml").read_text(encoding="utf-8")
    assert "scripts/check_minimum_home_assistant.py" in workflow
    assert "2025." not in workflow
    assert "2026." not in workflow
