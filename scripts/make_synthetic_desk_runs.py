#!/usr/bin/env python3
"""Write ``desk_runs.json`` for the Desk page from invented answers: no source is read, no model is called, nothing comes from RYO.

The answers of the RYO tools are made by the test world (``tests/desk_harness_checks.recordings_dir``) in the format of the real service,
with numbers that were chosen for this file. The Desk reads them through its recorded path, as it reads stored captures, and every
record of every run says ``synthetic``. They show what a run looks like (the claim, the checks, the weakest part, what was tried to
disprove it, the ending); they say nothing about any market.

usage: python scripts/make_synthetic_desk_runs.py OUT.json
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from tests.desk_harness_checks import recordings_dir, throwaway_ledger

from hoshitori import canonical, desk_harness, desk_plan, desk_view

# (symbol, direction, hours, the invented one-hour change in percent): one claim the answer supports, one it does not, one it contradicts
RUNS = (("SOL", "UP", 1, 0.30), ("ETH", "UP", 1, 0.05), ("BTC", "UP", 1, -0.40))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write(__doc__ or "")
        return 2
    records: list[tuple[str, dict[str, canonical.JsonValue]]] = []
    with tempfile.TemporaryDirectory() as scratch:
        for number, (symbol, direction, hours, change) in enumerate(RUNS, start=1):
            here = Path(scratch) / f"world{number}"
            here.mkdir()
            loaded = desk_harness.load_ryo_recordings(recordings_dir(here, symbol=symbol, change_1h=change))
            trigger = desk_plan.Trigger(f"syn-trg-{number}", "EVENT", symbol, direction, hours, None)
            ledger = throwaway_ledger(here / "run")
            outcome = desk_harness.run_recorded(loaded, ledger, trigger, f"syn-run-{number}", desk_harness.SYNTHETIC_LABEL)
            records.extend(outcome.records)
            said = f"{symbol} {direction} {hours} h (invented change {change}): {outcome.state} {outcome.reason}"
            sys.stdout.write(f"syn-run-{number}: {said}\n")
    Path(argv[0]).write_bytes(desk_view.file_bytes(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
