"""Child process for the kill tests: runs a ledger workload and dies (os._exit or SIGKILL from the parent).

Usage: ``python -I kill_child.py DIRECTORY SEGMENT_RECORDS [POINT:NTH | forever]``. Every acknowledged command is
printed (and flushed) as ``ACK <id>`` only after ``submit`` has returned, so the parent knows exactly which commands
the ledger promised to keep.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from hoshitori.clock import SystemClock
from hoshitori.ledger import Ledger
from hoshitori.note import Signer

ORIGIN = "example.org/hoshitori-test-ledger"
SIGNER = Signer.from_seed(ORIGIN, bytes(range(32)))


def rec(i: int) -> dict:  # type: ignore[type-arg]
    return {
        "spec": "hoshitori.record/1",
        "type": "evidence",
        "type_version": 1,
        "id": f"ev{i}",
        "source": "urn:hoshitori:test",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "body": {"n": i},
    }


def main() -> None:
    directory, records, mode = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    hits = {"n": 0}
    point, nth = mode.split(":") if mode != "forever" else ("", "0")

    def hook(name: str) -> None:
        if name == point:
            hits["n"] += 1
            if hits["n"] == int(nth):
                os._exit(9)  # equals SIGKILL for this process: no cleanup, nothing buffered in user space survives

    led = Ledger.open(directory, signer=SIGNER, origin=ORIGIN, clock=SystemClock(), segment_records=records, crash_hook=hook)
    start = led.size + led.pending()
    batches = 10**9 if mode == "forever" else 2
    n = start
    for _ in range(batches):
        for _ in range(8 if mode != "forever" else 3):
            receipt = led.submit(rec(n))
            print(f"ACK {receipt.record_id}", flush=True)
            n += 1
        led.append_pending()
        led.checkpoint()


if __name__ == "__main__":
    main()
