"""Real process kills: the recovery invariant must hold whatever the writer was doing when it died.

A killed process keeps the operating system's cache, so this shows process-crash safety only, not power-loss
durability (that needs a different test and has not been run).
"""

from __future__ import annotations

import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hoshitori import ledger
from tests.ledger_checks import ORIGIN, SIGNER, invariants, record_ids
from tests.support import ROOT

CHILD = ROOT / "tests" / "kill_child.py"
POINTS = (
    "after_journal_before_outbox",
    "before_append",
    "mid_line",
    "after_write_before_fsync",
    "after_fsync_before_index",
    "after_index_commit",
    "after_rotation",
    "before_checkpoint_store",
)


def run_child(directory: Path, records: int, mode: str, kill_after: float | None = None) -> list[str]:
    env = {"PATH": "/usr/bin:/bin"}
    proc = subprocess.Popen(
        [sys.executable, "-I", str(CHILD), str(directory), str(records), mode],
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdout is not None
    if kill_after is not None:
        time.sleep(kill_after)
        proc.send_signal(signal.SIGKILL)
    out, _ = proc.communicate(timeout=120)
    return [line.split()[1] for line in out.splitlines() if line.startswith("ACK ")]


def recover_and_check(directory: Path, records: int, acknowledged: list[str]) -> list[str]:
    from hoshitori.clock import SystemClock

    led = ledger.Ledger.open(directory, signer=SIGNER, origin=ORIGIN, clock=SystemClock(), segment_records=records)
    bad: list[str] = []
    try:
        if led.state != "OK":
            return [f"recovery ended in {led.state}: {led.state_detail}"]
        led.append_pending()
        ids = record_ids(directory)
        bad += [f"acknowledged record lost: {i}" for i in acknowledged if i not in ids]
        bad += invariants(directory, led)
        if len(ids) != len(set(ids)):
            bad.append("duplicate record")
    finally:
        led.abandon()
    return bad


@pytest.mark.parametrize("point", POINTS)
def test_kill_at_a_named_point(tmp_path: Path, point: str) -> None:
    acknowledged = run_child(tmp_path, 3, f"{point}:2")
    assert recover_and_check(tmp_path, 3, acknowledged) == []


@pytest.mark.slow
@pytest.mark.parametrize("point", POINTS)
@pytest.mark.parametrize("nth", [1, 3, 5, 9])
def test_kill_at_every_named_point_and_count(tmp_path: Path, point: str, nth: int) -> None:
    acknowledged = run_child(tmp_path, 3, f"{point}:{nth}")
    assert recover_and_check(tmp_path, 3, acknowledged) == []


def test_kill_at_random_moments(tmp_path: Path) -> None:
    rng = random.Random(1029)
    for round_ in range(3):
        directory = tmp_path / f"round-{round_}"
        records = rng.choice([2, 3, 5, 50])
        acknowledged = run_child(directory, records, "forever", kill_after=rng.uniform(0.15, 0.6))
        assert recover_and_check(directory, records, acknowledged) == [], f"round {round_}"
        # the log keeps working after recovery: kill it a second time on the same directory
        more = run_child(directory, records, "forever", kill_after=rng.uniform(0.15, 0.4))
        assert recover_and_check(directory, records, acknowledged + more) == [], f"round {round_} (second kill)"


@pytest.mark.slow
def test_many_random_kills(tmp_path: Path) -> None:
    rng = random.Random(8020261029)
    for round_ in range(int(os.environ.get("KILL_ROUNDS", "150"))):
        directory = tmp_path / f"round-{round_}"
        records = rng.choice([2, 3, 4, 7, 50])
        acknowledged = run_child(directory, records, "forever", kill_after=rng.uniform(0.1, 0.5))
        assert recover_and_check(directory, records, acknowledged) == [], f"round {round_}"
