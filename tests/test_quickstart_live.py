"""The quickstart end to end against the real exchanges and time-stamp authorities (``pytest -m live``): a stranger's first run.

It needs the network and takes minutes, so it is never part of the default set. It checks what the README promises: one cycle committed with
independent time-stamp tokens, the interface's documents written and every one of them matching its digest in the index, and the cycle's bundle
accepted by the offline verifier against the root the run made.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.live
def test_a_stranger_can_start_a_league_and_verify_its_first_cycle(tmp_path: Path) -> None:
    work = tmp_path / "work"
    done = subprocess.run(
        [sys.executable, "scripts/quickstart.py", str(work), "--days", "14"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert done.returncode == 0, done.stderr[-2000:]
    status = subprocess.run(
        [sys.executable, "-m", "hoshitori.league_cli", "status", str(work / "settings.json")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    seen = json.loads(status.stdout)
    assert seen["cycles"] == {"COMMITTED": 1} and seen["failures"] == 0 and seen["forecasts"] > 0
    site = work / "site"
    index = json.loads((site / "index.json").read_text())
    for name, digest in index["files"].items():
        assert hashlib.sha256((site / name).read_bytes()).hexdigest() == digest, name
    assert "drill.json" in index["files"], "the failure drills are part of the first export"
    drills = json.loads((site / "drill.json").read_text())
    assert drills and all(d["held"] for d in drills), "every drill held"
    (cycle,) = [p for p in (site / "bundles").iterdir() if p.is_dir()]
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    checked = subprocess.run(
        [sys.executable, "-m", "hoshitori.cli", str(cycle), "--trusted-root", str(work / "root.json"), "--now", now],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout[-1500:] + checked.stderr[-500:]
