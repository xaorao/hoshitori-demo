#!/usr/bin/env python3
"""Run the Desk on the recorded RYO reads and write its decision log as ``desk_runs.json``, for the Desk page.

Nothing is read live and no model is called: the checks are answered from the stored captures of the RYO tools, the clock stands at
the moment they were recorded, and the proposal is a flat position. Each run is labelled recorded and commits nothing. Usage:
python scripts/make_desk_runs.py RECORDINGS_DIR OUT.json
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

from hoshitori import canonical, desk_harness, desk_plan, desk_view

throwaway_ledger = importlib.import_module("tests.desk_harness_checks").throwaway_ledger

TRIGGERS = (
    ("SOL", "UP", 1),
    ("SOL", "DOWN", 1),
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(__doc__ or "")
        return 2
    loaded = desk_harness.load_ryo_recordings(Path(argv[0]))
    records: list[tuple[str, dict[str, canonical.JsonValue]]] = []
    with tempfile.TemporaryDirectory() as scratch:
        for number, (symbol, direction, hours) in enumerate(TRIGGERS, start=1):
            trigger = desk_plan.Trigger(f"trg-{number}", "EVENT", symbol, direction, hours, None)
            ledger = throwaway_ledger(Path(scratch) / f"run{number}")
            outcome = desk_harness.run_recorded(loaded, ledger, trigger, f"run-{number}")
            records.extend(outcome.records)
            sys.stdout.write(f"run-{number}: {symbol} {direction} {hours} h: {outcome.state} {outcome.reason}\n")
    Path(argv[1]).write_bytes(desk_view.file_bytes(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
