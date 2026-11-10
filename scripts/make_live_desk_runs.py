#!/usr/bin/env python3
"""Run the Desk on live reads and write its decision log as ``desk_runs.json``, for the Desk page.

The checks are RYO's tools, sent through the league's real gateway (the RYO key is read from the environment variable the
settings name, never from a file here). Each run takes minutes: the measured answers take 40 to 160 seconds. Nothing is time-
stamped or settled, the proposal is a model's when the settings pin one (else a flat position), and every record says ``live``.
Usage: python
scripts/make_live_desk_runs.py SETTINGS OUT.json SYMBOL:DIRECTION:HOURS[:MINUTES_AGO][,...] [RYO_CALL_CAP]
Use a copy of a league's folder, never a league
that is running (they would share one ledger lock).
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import time
from pathlib import Path

from hoshitori import canonical, deployment, desk_live, desk_model, desk_plan, desk_view
from hoshitori.ryo_pin import PIN

throwaway_ledger = importlib.import_module("tests.desk_harness_checks").throwaway_ledger


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        sys.stderr.write(__doc__ or "")
        return 2
    settings = deployment.load_settings(Path(argv[0]))
    triggers = [tuple(part.split(":")) for part in argv[2].split(",")]
    cap = int(argv[3]) if len(argv) == 4 else 3  # the most RYO calls a run may make
    records: list[tuple[str, dict[str, canonical.JsonValue]]] = []
    stamp = format(int(time.time()) // 60, "x")  # one name for this execution, so runs made at different times never share an identifier
    with deployment.assemble(settings) as running, tempfile.TemporaryDirectory() as scratch:
        if running.ryo is None:
            sys.stderr.write("these settings do not configure RYO\n")
            return 2
        models = running.services.models
        proposer = None
        if models is not None and models.l1 is not None:
            proposer = desk_model.make_proposer(
                models.client,
                models.l1.model,
                max_tokens=models.l1.max_tokens,
                thinking=models.l1.thinking,
                response_format=models.l1.response_format,
            )
        for number, (symbol, direction, hours, *ago) in enumerate(triggers, start=1):
            event_ms = int(time.time() * 1000) - int(ago[0]) * 60_000 if ago else None  # a stated announcement time: minutes ago
            trigger = desk_plan.Trigger(f"live-trg-{stamp}-{number}", "EVENT", symbol, direction, int(hours), event_ms)
            ledger = throwaway_ledger(Path(scratch) / f"run{number}")
            outcome = desk_live.run_live(
                running.services.env,
                ledger,
                trigger,
                f"live-run-{stamp}-{number}",
                frozenset(PIN),
                ryo_calls=cap,
                proposer=proposer,
                tokens=8000 if proposer else 1,
            )
            records.extend(outcome.records)
            sys.stdout.write(f"live-run-{number}: {symbol} {direction} {hours} h: {outcome.state} {outcome.reason}\n")
            sys.stdout.flush()
    Path(argv[1]).write_bytes(desk_view.file_bytes(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
