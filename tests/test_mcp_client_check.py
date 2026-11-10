"""The official MCP client can drive every standalone skill (``scripts/mcp_client_check.py``); skipped where the client is not installed."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mcp_client_check.py"


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="the official MCP client is not installed")
def test_every_skill_is_driven_by_the_official_client() -> None:
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.count(": ok") >= 7, done.stdout
