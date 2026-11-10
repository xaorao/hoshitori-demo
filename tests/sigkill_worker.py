"""A league process that kills itself with SIGKILL at a named place, so that what it leaves on disk is what a real kill leaves.

Run as ``python -m tests.sigkill_worker ROOT KIND``: it builds the scheduler test world in ROOT, runs one tick and, at the place KIND names
(the same four places the in-process kill tests use), the process is killed by the operating system's signal that nothing can catch: no
``finally`` block, no context manager and no exit handler runs, and nothing is flushed that was not already written.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from hoshitori import league, scheduler
from hoshitori.timeline import from_ms
from tests import league_checks as lc
from tests import scheduler_checks as sc


class _Kill(BaseException):
    """Stands in for the exception the in-process kills raise: making one kills the process."""

    def __new__(cls) -> _Kill:
        os.kill(os.getpid(), signal.SIGKILL)
        raise SystemExit  # never reached: the signal ends the process first


def main(argv: list[str]) -> int:
    root, kind = Path(argv[0]), argv[1]
    sc.Died = _Kill  # type: ignore[assignment,misc]
    w = sc.world(root)
    cfg = sc.config(scheduler, root)
    services = w.services(league)
    w.clock.set(from_ms(lc.SLOT + 20_000))
    if kind == "before_batch_append":
        sc.die_before_batch_append(w)
    elif kind == "after_group_journaled":
        sc.die_after_group_journaled(w)
    elif kind == "before_group_journaled":
        sc.die_before_group_journaled(w)
    elif kind == "batch_never_written":
        real = w.ledger.submit_many

        def refuse(records: object) -> object:
            if any(r.get("type") == "snapshot" for r in records):  # type: ignore[attr-defined]
                _Kill()
            return real(records)  # type: ignore[arg-type]

        w.ledger.submit_many = refuse  # type: ignore[method-assign]
    scheduler.tick(services, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
