"""Build the rules arm's barrier mapping from real history: ``python scripts/build_barrier_mapping.py``.

    build_barrier_mapping.py --venue binance-spot --quote-class USDT --instruments BTCUSDT,ETHUSDT --days 14 --out barrier.json --work DIR

The job reads the hourly and the one-minute history of one venue's instruments through the gateway (public candles, no key), counts the
first-touch windows (``barrier_rates.calibrate``) and writes the mapping as a canonical file with its hash. It prints a summary and the
failures. The instruments are the protocol's, named on the command line. The mapping is exploratory until a protocol pins its hash; running
the job again later gives another mapping with another hash, never an edit of the first.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from pathlib import Path

from hoshitori import barrier_rates, history
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor
from hoshitori.timeline import to_ms
from hoshitori.venue_base import VenueConfig
from hoshitori.venues import VENUES, attach


def summary(rates: barrier_rates.BarrierRates, hourly: history.History, minutes: history.History) -> dict[str, object]:
    """What the job prints: the hash, the size of both histories and, per horizon, the counts and the mapped probabilities."""
    return {
        "sha256": rates.sha256,
        "venue": rates.venue,
        "instruments": list(rates.instruments),
        "hourly_bars": {name: len(bars) for name, bars in hourly.bars},
        "minute_bars": {name: len(bars) for name, bars in minutes.bars},
        "skipped": rates.skipped,
        "failures": [
            {"instrument": f.instrument, "page_end": f.page_end.isoformat(), "reason": f.reason.value, "detail": f.detail}
            for f in (*hourly.failures, *minutes.failures)
        ],
        "horizons": {
            str(h): {
                "target": c.target,
                "stop": c.stop,
                "timeout": c.timeout,
                "ambiguous": c.ambiguous,
                "p": None if rates.p(h) is None else [str(x) for x in rates.p(h) or ()],
            }
            for h, c in rates.cells
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_barrier_mapping", description="Build the rules arm's barrier mapping from history.")
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
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    clock = SystemClock()
    args.work.mkdir(parents=True, exist_ok=True)
    store = CaptureStore(args.work / "captures")
    gateway = Gateway(store, DispatchJournal(args.work / "dispatches.jsonl"), retry_policy=RetryPolicy(), rng=random.Random())  # noqa: S311
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
    names = args.instruments.split(",")
    try:
        source = history.Source(runner, store, attached.adapter, clock)
        hourly = history.fetch_hourly(source, names, days=args.days, wait_s=args.wait)
        minutes = history.fetch_minutes(source, names, hours=args.days * 24, wait_s=args.wait)
    except history.HistoryError as exc:
        print(f"the history could not be read: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.stop()
        loop.join(timeout=5)
        pool.shutdown()
        store.close()
    firsts = [bars[0].open_time for _, bars in hourly.bars if bars]
    start = to_ms(min(firsts)) if firsts else to_ms(hourly.end)
    rates = barrier_rates.calibrate(
        hourly.histories(),
        minutes.histories(),
        venue=hourly.venue,
        quote_class=args.quote_class,
        period_start_ms=start,
        period_end_ms=to_ms(minutes.end),
    )
    args.out.write_bytes(barrier_rates.dump(rates))
    print(json.dumps(summary(rates, hourly, minutes), indent=2, sort_keys=True))
    return 1 if args.strict and (hourly.failures or minutes.failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
