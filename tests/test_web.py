"""The interface's own tests (``web/test``, Node's test runner): wording, addresses and the check of an exported document."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None or not (WEB / "node_modules").is_dir(), reason="needs node and `npm ci` in web/")
def test_the_interface_modules_pass_their_tests() -> None:
    assert NODE is not None
    files = sorted(str(p.relative_to(WEB)) for p in (WEB / "test").glob("*.test.ts"))
    assert files, "the interface has no tests"
    done = subprocess.run([NODE, "--test", *files], cwd=WEB, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
