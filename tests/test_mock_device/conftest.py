"""Shared fixtures for mock_device tests.

Note: Test folder is named mock_device but we import from tools/mock_device.
The path manipulation in each test file handles this.
"""

from __future__ import annotations

import sys
from pathlib import Path

_tools_dir = Path(__file__).parent.parent.parent / "tools"
if str(_tools_dir) not in sys.path:
	sys.path.insert(0, str(_tools_dir))
