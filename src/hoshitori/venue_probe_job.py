"""Run the venue probe from this host: ``python scripts/probe_venues.py --venue kraken --instruments XBTUSDT,ETHUSDT --minutes 60``.

It prints the finding as JSON and exits 0 when the venue can settle, 1 when it cannot (unreachable, or a bar missing for too many of the
closed minutes), 2 for a request that cannot be made. Public reads only, no key. Run it on the host that will run the league: a venue can be
blocked on one network and open on another (CF-60).
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
import threading
from pathlib import Path
from typing import Any

from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.history import Source
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.venue_base import VenueConfig
from hoshitori.venue_probe import ProbeError, VenueFinding, probe
from hoshitori.venues import VENUES, attach


def as_json(found: VenueFinding) -> dict[str, Any]:
    return {
        "venue": found.venue,
        "reachable": found.reachable,
        "problem": found.problem,
        "can_settle": found.can_settle,
        "instruments": [
            {
                "instrument": f.instrument,
                "problem": f.problem,
                "closed_slots": None if f.coverage is None else f.coverage.closed_slots,
                "present": None if f.coverage is None else f.coverage.present,
                "present_share": None if f.coverage is None or f.coverage.present_share is None else str(f.coverage.present_share),
                "padded": None if f.coverage is None else f.coverage.padded,
                "can_settle": None if f.coverage is None else f.coverage.can_settle,
            }
            for f in found.instruments
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="probe_venues", description="Can this host settle against this venue?")
    parser.add_argument("--venue", required=True, choices=sorted(VENUES))
    parser.add_argument("--instruments", required=True, help="comma-separated venue instrument ids")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--wait", type=float, default=30.0)
    parser.add_argument("--base-url", help="a different base URL (a test server)")
    parser.add_argument("--allow-loopback", action="store_true", help="allow an http loopback base URL (tests only)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    clock = SystemClock()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        store = CaptureStore(work / "captures")
        gateway = Gateway(store, DispatchJournal(work / "dispatches.jsonl"), retry_policy=RetryPolicy(), rng=random.Random())  # noqa: S311
        attached = attach(
            gateway,
            args.venue,
            clock,
            base_url=args.base_url,
            config=VenueConfig(),
            breaker=BreakerConfig(),
            allow_insecure_loopback=args.allow_loopback,
        )
        pool = ThreadExecutor(4)
        runner = Runner(gateway, {attached.adapter.provider: attached.transport}, clock, pool)
        loop = threading.Thread(target=runner.run_forever, daemon=True)
        loop.start()
        try:
            found = probe(
                Source(runner, store, attached.adapter, clock), args.instruments.split(","), minutes=args.minutes, wait_s=args.wait
            )
        except ProbeError as exc:
            print(f"probe_venues: {exc}")
            return 2
        finally:
            runner.stop()
            loop.join(timeout=5)
            pool.shutdown()
            store.close()
    print(json.dumps(as_json(found), indent=2, sort_keys=True))
    return 0 if found.can_settle else 1


if __name__ == "__main__":
    raise SystemExit(main())
