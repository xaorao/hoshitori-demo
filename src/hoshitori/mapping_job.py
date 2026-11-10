"""Build the rules arm's frozen mapping from real hourly history: ``python scripts/build_mapping.py``.

    build_mapping.py --venue binance-spot --quote-class USDT --instruments BTCUSDT,ETHUSDT,... --days 90 --out mapping.json --work DIR

The job reads the history through the gateway (one venue, public candles, no key), counts the windows (``rules_arm.calibrate``) and writes
the mapping as a canonical file with its hash (``rules_arm.dump``). It prints a summary and the failures. It does not choose the universe:
the instruments are the protocol's, named on the command line. The mapping is exploratory until a protocol pins its hash; running the job
again later gives another mapping with another hash, never an edit of the first.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from pathlib import Path

from hoshitori import history, rules_arm
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.timeline import to_ms
from hoshitori.venue_base import VenueConfig
from hoshitori.venues import VENUES, attach


def summary(rates: rules_arm.BaseRates, got: history.History) -> dict[str, object]:
    """What the job prints: the hash, the size of the history and, per horizon, the windows and the mapped probability of each state."""
    return {
        "sha256": rates.sha256,
        "venue": rates.venue,
        "instruments": list(rates.instruments),
        "bars": {name: len(bars) for name, bars in got.bars},
        "skipped": rates.skipped,
        "failures": [
            {"instrument": f.instrument, "page_end": f.page_end.isoformat(), "reason": f.reason.value, "detail": f.detail}
            for f in got.failures
        ],
        "horizons": {
            str(h): {s: {"n": rates.cell(h, s).n, "p": None if rates.p(h, s) is None else str(rates.p(h, s))} for s in rules_arm.STATES}
            for h in rules_arm.HORIZONS
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_mapping", description="Build the rules arm's frozen mapping from hourly history.")
    parser.add_argument("--venue", required=True, choices=sorted(VENUES))
    parser.add_argument("--quote-class", required=True)
    parser.add_argument("--instruments", required=True, help="comma-separated venue instrument ids")
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True, help="a directory for the captures of this run")
    parser.add_argument("--base-url", help="a different base URL (a test server)")
    parser.add_argument("--allow-loopback", action="store_true", help="allow an http loopback base URL (tests only)")
    parser.add_argument("--wait", type=float, default=120.0)
    parser.add_argument("--strict", action="store_true", help="exit with status 1 if any page could not be read")
    args = parser.parse_args(argv)
    clock = SystemClock()
    args.work.mkdir(parents=True, exist_ok=True)
    store = CaptureStore(args.work / "captures")
    gateway = Gateway(store, DispatchJournal(args.work / "dispatches.jsonl"), retry_policy=RetryPolicy(), rng=random.Random())  # noqa: S311 (retry jitter, not a secret)
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
        got = history.fetch_hourly(
            history.Source(runner, store, attached.adapter, clock), args.instruments.split(","), days=args.days, wait_s=args.wait
        )
    except history.HistoryError as exc:
        print(f"the history could not be read: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.stop()
        loop.join(timeout=5)
        pool.shutdown()
        store.close()
    firsts = [bars[0].open_time for _, bars in got.bars if bars]
    start = to_ms(min(firsts)) if firsts else to_ms(got.end)
    rates = rules_arm.calibrate(
        got.histories(), venue=got.venue, quote_class=args.quote_class, period_start_ms=start, period_end_ms=to_ms(got.end)
    )
    args.out.write_bytes(rules_arm.dump(rates))
    print(json.dumps(summary(rates, got), indent=2, sort_keys=True))
    return 1 if args.strict and got.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
