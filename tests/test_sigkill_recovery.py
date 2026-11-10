"""A process killed for real (SIGKILL) at each place a cycle can be cut leaves a ledger that a new process recovers from (plan unit 2.4).

The in-process kill tests raise an exception that nothing catches, which still lets ``finally`` blocks and context managers run. Here a
separate process runs the league and kills itself with the signal that no code can intercept; a second process then opens the same
files, runs the tick again and is asked the questions that must hold: the cycle ends, a cycle has all of its forecasts or none, no
forecast that was committed is lost, nothing acknowledged is left queued, every history reads. This covers a process kill, not a power cut:
the operating system keeps what the process had written.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from hoshitori import cycle, league, lifecycle, scheduler
from hoshitori.timeline import from_ms
from tests import league_checks as lc
from tests import scheduler_checks as sc

REPO = Path(__file__).resolve().parents[1]
KINDS = ("before_batch_append", "after_group_journaled", "before_group_journaled", "batch_never_written")


def _kill_a_process(root: Path, kind: str) -> int:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run(
        [sys.executable, "-m", "tests.sigkill_worker", str(root), kind], cwd=REPO, env=env, capture_output=True, check=False
    )
    return done.returncode


@pytest.mark.parametrize("kind", KINDS)
def test_a_process_killed_for_real_is_recovered_from(kind: str, tmp_path: Path) -> None:
    code = _kill_a_process(tmp_path, kind)
    assert code == -signal.SIGKILL, f"the worker was not killed by SIGKILL (exit {code})"
    w = sc.world(tmp_path)  # a new process opens the same files
    w.runner._n = 1_000_000  # type: ignore[attr-defined]  # the test runner numbers its captures from 1 in every process; the real gateway draws random ids
    try:
        services, cfg = w.services(league), sc.config(scheduler, tmp_path)
        w.clock.set(from_ms(lc.SLOT + 15 * 60_000))
        scheduler.tick(services, cfg)
        w.clock.set(from_ms(lc.SLOT + lc.HOUR + 20_000))
        scheduler.tick(services, cfg)  # and the next slot, so anything left to close is closed
        records = scheduler.read_ledger(w.ledger)
        cycles = cycle.rebuild([r for r in records if r.get("type") == cycle.TYPE])
        slots = lifecycle.rebuild([r for r in records if r.get("type") == lifecycle.TYPE])
        forecasts: dict[str, int] = {}
        made: set[str] = set()
        for r in records:
            if r.get("type") == "forecast":
                forecasts[str(r["body"]["cycle_id"])] = forecasts.get(str(r["body"]["cycle_id"]), 0) + 1
                made.add(str(r["id"]))
        assert w.ledger.pending() == 0, "something acknowledged was left queued"
        assert cycles, "no cycle is in the ledger"
        for cid, history in cycles.items():
            assert cycle.current_state(history) in cycle.TERMINAL, f"cycle {cid} was left open"
            assert forecasts.get(cid, 0) in (0, 6), f"cycle {cid} holds {forecasts.get(cid, 0)} forecasts: neither all nor none"
        lost = [fid for fid, h in slots.items() if h[-1].step.state.value == "FAILED_LOST" and fid in made]
        assert lost == [], f"a forecast that was committed was recorded as lost: {lost}"
    finally:
        w.close()
